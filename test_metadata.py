import json
import tempfile
import unittest
import wave
import asyncio
import importlib.util
import sys
import types
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path
from scripts.generate_metadata import generate, company_profile, save_json
from scripts.fiscal_period import candidates


def rows(url):
    return [{"公司代號": "4104", "公司名稱": "佳醫健康事業股份有限公司", "產業別": "22"}]


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root / "4104_20260915.mp4"
        self.folder = self.root / "output/4104_20260915"
        self.folder.mkdir(parents=True)

    def generate(self, **kwargs):
        return generate(self.video, root=self.root, fetch=rows, search_announcements=False, **kwargs)

    def test_filename_and_pending_period(self):
        data = self.generate()
        self.assertEqual(data["call_date"], "2026-09-15")
        self.assertEqual(data["company_code"], "4104")
        self.assertEqual(data["industry"], "生技醫療業")
        self.assertIsNone(data["fiscal_year"])
        self.assertEqual(data["fiscal_period_status"], "pending")

    def test_periods(self):
        for text in ("115年第二季財務報告", "2026 Q2 earnings", "Q2 2026 results"):
            found = candidates(text,"test")
            self.assertEqual((found[0]["fiscal_year"],found[0]["fiscal_quarter"]),(2026,"Q2"))
        self.assertEqual(len(candidates("2026 Q2 earnings; 2025 Q2 earnings","test")),2)
        self.assertEqual(candidates("2026年9月法說會","test"),[])

    def test_cache_avoids_network(self):
        self.generate()
        def offline(url):
            self.fail("fresh cache must not access network")
        result, warning = company_profile("4104", self.root / "data/cache/company_profiles.json", offline)
        self.assertEqual(result["company_name"], "佳醫健康事業股份有限公司")
        self.assertIsNone(warning)

    def test_offline_and_stale_cache(self):
        cache = self.root / "cache.json"
        def offline(url):
            raise OSError("offline")
        profile, warning = company_profile("4104", cache, offline)
        self.assertEqual(profile, {})
        self.assertTrue(warning)
        save_json(cache, {"4104": {"company_name": "saved", "fetched_at": "2020-01-01T00:00:00+00:00"}})
        profile, warning = company_profile("4104", cache, offline)
        self.assertEqual(profile["company_name"], "saved")
        self.assertTrue(warning)

    def test_source_and_existing_values(self):
        path = self.folder / "4104_20260915_metadata.json"
        save_json(path, {"company_name": "Manual", "fiscal_year": 2025, "fiscal_quarter": "Q4", "custom": True})
        save_json(self.video.with_suffix(".info.json"), {"webpage_url": "https://example.org/video"})
        data = self.generate()
        self.assertEqual(data["company_name"], "Manual")
        self.assertEqual(data["source_url"], "https://example.org/video")
        self.assertEqual(data["fiscal_year"], 2025)
        self.assertTrue(data["custom"])
        self.assertEqual(self.generate(source_url="https://example.org/new")["source_url"], "https://example.org/new")

    def test_audio_and_announcement_priority(self):
        save_json(self.folder / "4104_20260915_raw.json", {"language": "zh", "text": "2025 Q1 earnings"})
        with wave.open(str(self.folder / "4104_20260915.wav"), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\0\0" * 32000)
        announcement = self.video.with_suffix(".announcement.txt")
        announcement.write_text("2026年第二季財務報告", encoding="utf-8")
        data = self.generate()
        self.assertEqual(data["audio_duration_sec"], 2)
        self.assertEqual(data["language"], "zh")
        self.assertEqual((data["fiscal_year"], data["fiscal_quarter"]), (None, None))
        self.assertEqual(data["fiscal_period_status"], "ambiguous")
        self.assertEqual({c["fiscal_year"] for c in data["fiscal_period_candidates"]}, {2025, 2026})

    def test_legacy_incomplete_period_not_invented(self):
        save_json(self.folder / "4104_20260915_metadata.json", {"fiscal_quarter": "Q3"})
        data = self.generate()
        self.assertIsNone(data["fiscal_year"])
        self.assertEqual(data["fiscal_quarter"], "Q3")

    def test_invalid_date(self):
        with self.assertRaises(ValueError):
            generate(self.root / "4104_20260230.mp4", root=self.root, fetch=rows)

    def test_database_insert_and_update_use_metadata_period(self):
        # Exercise SQL bindings without touching a real database or requiring its driver.
        spec = importlib.util.spec_from_file_location("metadata_db_test", Path(__file__).parent / "scripts/transcript_to_db.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"aiomysql": types.SimpleNamespace(), "dotenv": types.SimpleNamespace(load_dotenv=lambda *args: None)}):
            spec.loader.exec_module(module)
        path = self.folder / "4104_20260915_metadata.json"
        save_json(path, {"company_name": "Company", "fiscal_year": 2025, "fiscal_quarter": "Q4", "fiscal_period_status": "manual"})

        class Cursor:
            def __init__(self, exists, nullable=True):
                self.exists, self.nullable, self.calls = exists, nullable, []
            async def execute(self, sql, params=None):
                self.calls.append((sql, params))
                if params is not None:
                    assert sql.count("%s") == len(params)
            async def fetchone(self):
                return ("4104_20260915",) if self.exists else None
            async def fetchall(self):
                return [(name, "varchar(255)", "YES" if self.nullable else "NO")
                        for name in ("company_name", "fiscal_year", "fiscal_quarter")]

        for exists in (False, True):
            cursor = Cursor(exists)
            asyncio.run(module.ensure_earnings_call(cursor, "4104_20260915", "zh", path, self.folder / "missing.wav"))
            sql, params = cursor.calls[-1]
            self.assertIn(2025, params)
            self.assertIn("Q4", params)
            self.assertNotIn(2026, params)
        save_json(path, {"company_name": "Company", "fiscal_year": None, "fiscal_quarter": None})
        cursor = Cursor(False)
        asyncio.run(module.ensure_earnings_call(cursor, "4104_20260915", "zh", path, self.folder / "missing.wav"))
        self.assertEqual(cursor.calls[-1][1][4:6], (None, None))
        with self.assertRaisesRegex(RuntimeError, "allow_pending_metadata"):
            asyncio.run(module.ensure_earnings_call(Cursor(False, False), "4104_20260915", "zh", path, self.folder / "missing.wav"))


if __name__ == "__main__":
    unittest.main()
