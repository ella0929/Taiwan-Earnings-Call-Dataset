import asyncio
import importlib.util
import json
import sys
import tempfile
import types
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock
from scripts.pipeline_common import fingerprint, cache_matches, save_cache, save_json, read_json
from scripts.qa_rules import classify_segments
from scripts.announcement_lookup import lookup, meeting_date
from scripts.generate_metadata import generate
import run_all


def load_module(name, relative, stubs):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / relative)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module


def seg(i, speaker, text):
    return dict(segment_id=f"s{i}", call_id="4104_20260915", speaker_id=speaker,
                raw_text=text, start_time_sec=i*10, end_time_sec=i*10+9)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_same_name_source_change_invalidates_cache(self):
        source, artifact, manifest = [self.root / n for n in ("input.mp4", "audio.wav", "manifest.json")]
        source.write_bytes(b"first")
        artifact.write_bytes(b"audio")
        inputs = {"source": fingerprint(source)}
        save_cache(artifact, manifest, inputs)
        self.assertTrue(cache_matches(artifact, manifest, inputs))
        source.write_bytes(b"other")
        self.assertFalse(cache_matches(artifact, manifest, {"source": fingerprint(source)}))
        artifact.write_bytes(b"corrupt")
        self.assertFalse(cache_matches(artifact, manifest, inputs))

    def test_pipeline_stops_on_review_or_failure(self):
        for code in (1, 2):
            calls = []
            def runner(command, **kwargs):
                calls.append(command)
                return types.SimpleNamespace(returncode=code)
            status = self.root / "status.json"
            result = run_all.execute_steps([("speaker", ["speaker.py"]), ("qa", ["main.py"])], status, runner)
            self.assertEqual(result, code)
            self.assertEqual(len(calls), 1)
            self.assertEqual(read_json(status)["status"], "review_required" if code == 2 else "failed")

    def test_single_qc_and_order(self):
        args = types.SimpleNamespace(source_url=None, announcement=None, offline=False, force=True)
        steps = run_all.build_steps(Path("4104_20260915.wav"), args)
        scripts = [command[0] for _, command in steps]
        self.assertEqual(scripts.count("scripts/transcript_qc.py"), 1)
        self.assertLess(scripts.index("scripts/generate_metadata.py"), scripts.index("scripts/transcript_qc.py"))
        self.assertLess(scripts.index("speaker_processor.py"), scripts.index("main.py"))
        self.assertIn("--force", steps[0][1])

    def test_early_qa_multi_answer_host_interjection_followup(self):
        segments = [seg(0,"host","現在開放提問"), seg(1,"a","想請教營收展望"),
                    seg(2,"boss","營收會增加"), seg(3,"host","下一位請發問"),
                    seg(4,"cfo","補充毛利率情況"), seg(5,"a","我的問題是明年的成本"),
                    seg(6,"boss","成本預期穩定")]
        groups, issues = classify_segments(segments)
        self.assertFalse(issues)
        self.assertEqual([g["question_id"] for g in groups], [None,"Q_01","Q_01",None,"Q_01","Q_02","Q_02"])

    def test_no_merge_across_presentation_question_boundary(self):
        groups, _ = classify_segments([seg(0,"a","公司簡報"), seg(1,"a","想請教營收"), seg(2,"b","會成長")])
        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[0]["section_type"], "presentation")

    def test_unknown_speaker_and_empty_fail(self):
        for rows in ([], [seg(0,None,"test")], [seg(0,"a","")]):
            with self.assertRaises(ValueError):
                classify_segments(rows)

    def test_ambiguous_question_review_and_manual_override(self):
        rows = [seg(0,"a","營收會提高嗎？"), seg(1,"b","預期會成長")]
        _, issues = classify_segments(rows)
        self.assertTrue(issues)
        groups, issues = classify_segments(rows, {"s0": "question", "s1": "answer"})
        self.assertFalse(issues)
        self.assertEqual(groups[1]["question_id"], "Q_01")

    def test_announcements_match_event_date_not_publication_date(self):
        def fetch(url):
            return [{"公司代號":"4104", "主旨 ":"本公司召開法人說明會", "發言日期":"1150915",
                     "說明":"召開法人說明會日期：115/09/16；2026年第二季財務報告"}]
        records, _ = lookup("4104", "2026-09-15", self.root / "ann.json", fetch)
        self.assertEqual(records, [])
        records, _ = lookup("4104", "2026-09-16", self.root / "ann.json", fetch)
        self.assertTrue(records)
        self.assertEqual(meeting_date("發言日期：115/09/16"), None)
        def fail(url):
            self.fail("same-day cache should avoid network")
        records, _ = lookup("4104", "2026-09-16", self.root / "ann.json", fail)
        self.assertTrue(records)

    def test_metadata_overrides_survive_rerun_and_direct_edit(self):
        video = self.root / "4104_20260915.mp4"
        def fetch(url):
            return [{"公司代號":"4104","公司名稱":"Official","產業別":"22"}]
        kwargs = dict(root=self.root, fetch=fetch, search_announcements=False)
        data = generate(video, **kwargs)
        path = self.root / "output/4104_20260915/4104_20260915_metadata.json"
        data["company_name"] = "Edited"
        save_json(path, data)
        self.assertEqual(generate(video, **kwargs)["company_name"], "Edited")
        save_json(path.with_name("4104_20260915_metadata_overrides.json"), {"fiscal_year":2026,"fiscal_quarter":"Q2"})
        data = generate(video, **kwargs)
        self.assertEqual(data["fiscal_period_status"], "manual")
        self.assertEqual(generate(video, **kwargs)["fiscal_quarter"], "Q2")

    def test_sql_replace_rollback_on_insert_failure(self):
        qa = load_module("qa_test", "qa_processor.py", {"aiomysql":types.SimpleNamespace(), "dotenv":types.SimpleNamespace(load_dotenv=lambda *a:None)})
        class Cursor:
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
            async def execute(self,*args): pass
            async def fetchone(self): return ("InnoDB",)
            async def executemany(self,*args): raise RuntimeError("insert failed")
        conn = types.SimpleNamespace(begin=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock(), cursor=lambda:Cursor())
        groups,_ = classify_segments([seg(0,"a","簡報")])
        with self.assertRaisesRegex(RuntimeError, "insert failed"):
            asyncio.run(qa.replace_segments(conn,"4104_20260915",groups))
        conn.rollback.assert_awaited_once()
        conn.commit.assert_not_awaited()

    def test_speaker_false_returns_nonzero(self):
        fake = types.SimpleNamespace()
        speaker = load_module("speaker_test", "speaker_processor.py", {
            "aiomysql":fake,"numpy":fake,"torch":fake,
            "dotenv":types.SimpleNamespace(load_dotenv=lambda *a,**k:None),
            "pyannote":fake,"pyannote.audio":types.SimpleNamespace(Pipeline=fake)})
        with patch.object(speaker,"process_speakers",AsyncMock(return_value=False)), patch.object(sys,"argv",["speaker_processor.py","4104_20260915"]):
            self.assertEqual(asyncio.run(speaker.main()),2)

    def test_media_cache_reruns_when_source_or_force_changes(self):
        processor = load_module("scripts.video_test", "scripts/process_video.py", {
            "dotenv":types.SimpleNamespace(load_dotenv=lambda *a:None),
            "ctranslate2":types.SimpleNamespace(get_cuda_device_count=lambda:0),
            "faster_whisper":types.SimpleNamespace(WhisperModel=object)})
        source = self.root / "4104_20260915.mov"
        source.write_bytes(b"first")
        converted, transcribed = [], []
        def convert(src,dst):
            converted.append(src)
            dst.write_bytes(src.read_bytes()+b"audio")
        def transcribe(*args):
            transcribed.append(args)
            return {"segments":[{"start":0,"end":1,"text":"hello"}]}
        with patch.object(processor,"ROOT",self.root), patch.object(processor,"convert_to_wav",convert), patch.object(processor,"transcribe_audio",transcribe):
            processor.process_video(source)
            processor.process_video(source)
            self.assertEqual((len(converted),len(transcribed)),(1,1))
            source.write_bytes(b"other")
            processor.process_video(source)
            self.assertEqual((len(converted),len(transcribed)),(2,2))
            processor.process_video(source,force=True)
            self.assertEqual((len(converted),len(transcribed)),(3,3))

    def test_database_pending_preserves_real_existing_values(self):
        db = load_module("db_test", "scripts/transcript_to_db.py", {
            "dotenv":types.SimpleNamespace(load_dotenv=lambda *a:None),"aiomysql":types.SimpleNamespace()})
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute("""CREATE TABLE earnings_calls (call_id TEXT, language TEXT, audio_duration_sec REAL,
            company_name TEXT, industry TEXT, fiscal_year INTEGER, fiscal_quarter TEXT,
            source_url TEXT, audio_url TEXT, transcript_url TEXT, updated_at TEXT)""")
        conn.execute("INSERT INTO earnings_calls(call_id,fiscal_year,fiscal_quarter) VALUES('4104_20260915',2025,'Q4')")
        class Cursor:
            async def execute(self,sql,args=None):
                self.cursor = conn.execute(sql.replace("%s","?"),args or ())
            async def fetchone(self): return self.cursor.fetchone()
        metadata = self.root / "metadata.json"
        for status,year,quarter in (("pending",None,None),("inferred",2026,"Q2")):
            save_json(metadata,{"fiscal_period_status":status,"fiscal_year":year,"fiscal_quarter":quarter})
            asyncio.run(db.ensure_earnings_call(Cursor(),"4104_20260915","zh",metadata,self.root/"no.wav"))
            self.assertEqual(conn.execute("SELECT fiscal_year,fiscal_quarter FROM earnings_calls").fetchone(),(2025,"Q4"))
        save_json(metadata,{"fiscal_period_status":"manual","fiscal_year":2026,"fiscal_quarter":"Q2"})
        asyncio.run(db.ensure_earnings_call(Cursor(),"4104_20260915","zh",metadata,self.root/"no.wav"))
        self.assertEqual(conn.execute("SELECT fiscal_year,fiscal_quarter FROM earnings_calls").fetchone(),(2026,"Q2"))

    def test_mixed_host_question_marked_for_review(self):
        _,issues = classify_segments([seg(0,"a","現在開放提問，請問營收會增加嗎？"),seg(1,"b","會")])
        self.assertTrue(issues)

    def test_presentation_only_no_fake_qa(self):
        groups,issues = classify_segments([seg(0,"a","本季營收提高"),seg(1,"b","接下來說明營業費用")])
        self.assertFalse(issues)
        self.assertTrue(all(g["question_id"] is None for g in groups))

    def test_announcement_offline_failure_still_generates_metadata(self):
        def unavailable(url): raise OSError("offline")
        def company(url): return [{"公司代號":"4104","公司名稱":"Company","產業別":"22"}]
        data = generate(self.root/"4104_20260915.mp4",root=self.root,fetch=company,announcement_fetch=unavailable)
        self.assertEqual(data["company_name"],"Company")
        self.assertTrue(data["warnings"])
        self.assertIsNone(data["fiscal_year"])

    def test_diarization_cache_checks_audio(self):
        fake = types.SimpleNamespace()
        module = load_module("diarization_test", "speaker_processor.py", {
            "aiomysql":fake,"numpy":fake,"torch":fake,
            "dotenv":types.SimpleNamespace(load_dotenv=lambda *a,**k:None),
            "pyannote.audio":types.SimpleNamespace(Pipeline=fake)})
        audio, artifact = self.root/"audio.wav", self.root/"diarization.json"
        audio.write_bytes(b"first")
        calls = []
        def inference(path):
            calls.append(path)
            return [{"speaker":"a","start":0,"end":1}]
        with patch.object(module,"run_diarization",inference):
            module.get_diarization_results(self.root,audio,artifact)
            module.get_diarization_results(self.root,audio,artifact)
            self.assertEqual(len(calls),1)
            audio.write_bytes(b"other")
            module.get_diarization_results(self.root,audio,artifact)
            self.assertEqual(len(calls),2)

    def test_decimal_timestamps_serializable(self):
        from decimal import Decimal
        row = seg(0,"a","簡報")
        row.update(start_time_sec=Decimal("0.1"),end_time_sec=Decimal("1.5"))
        groups,_ = classify_segments([row])
        json.dumps(groups)


if __name__ == "__main__":
    unittest.main()
