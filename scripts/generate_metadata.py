"""Generate metadata without inventing reporting periods or company identities."""
import argparse
import csv
import io
import json
import re
import wave
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

try:
    from .announcement_lookup import lookup
    from .fiscal_period import candidates, validate_overrides
    from .pipeline_common import save_json
except ImportError:
    from announcement_lookup import lookup
    from fiscal_period import candidates, validate_overrides
    from pipeline_common import save_json

ROOT = Path(__file__).resolve().parents[1]
# TWSE industry classification; unknown codes remain pending rather than guessed.
INDUSTRIES = dict(zip(
    "01 02 03 04 05 06 08 09 10 11 12 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 35 36 37 38".split(),
    "水泥工業 食品工業 塑膠工業 紡織纖維 電機機械 電器電纜 玻璃陶瓷 造紙工業 鋼鐵工業 橡膠工業 汽車工業 建材營造 航運業 觀光餐旅 金融保險 貿易百貨 綜合 其他 化學工業 生技醫療業 油電燃氣業 半導體業 電腦及週邊設備業 光電業 通信網路業 電子零組件業 電子通路業 資訊服務業 其他電子業 綠能環保 數位雲端 運動休閒 居家生活".split(),
))
SOURCES = (
    "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
    "https://mopsfin.twse.com.tw/opendata/t187ap03_O.csv",
)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}


def fetch_rows(url):
    request = Request(url, headers={"User-Agent": "EarningsCallMetadata/1.0"})
    with urlopen(request, timeout=20) as response:
        text = response.read().decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text))) if url.endswith(".csv") else json.loads(text)
    if not isinstance(rows, list) or not rows or "公司代號" not in rows[0]:
        raise ValueError("公司資料回應格式不符")
    return rows


def company_profile(code, cache_path, fetch=fetch_rows):
    try:
        cache = read_json(cache_path)
    except (ValueError, OSError):
        cache = {}
    saved = cache.get(code)
    if saved:
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(saved["fetched_at"])
            if age.days < 30:
                return saved, None
        except (KeyError, ValueError, TypeError):
            pass
    errors = []
    for url in SOURCES:
        try:
            rows = fetch(url)
            for row in rows:
                key = str(row.get("公司代號", "")).strip()
                name = str(row.get("公司名稱", "")).strip()
                if not key or not name:
                    continue
                industry = str(row.get("產業別", "")).strip()
                cache[key] = {
                    "company_name": name,
                    "industry": INDUSTRIES.get(industry.zfill(2)) if industry.isdigit() else industry or None,
                    "industry_code": industry if industry.isdigit() else None,
                    "source": url,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
            save_json(cache_path, cache)
            if code in cache and cache[code] != saved:
                return cache[code], None
        except (OSError, ValueError, TypeError) as error:
            errors.append(f"{url}: {error}")
    if saved:
        return saved, "公司資料更新失敗，沿用舊快取"
    return {}, "公司資料查無結果或下載失敗：" + "; ".join(errors)


def generate(video, source_url=None, announcement=None, root=ROOT, fetch=fetch_rows,
             search_announcements=True, offline=False, announcement_fetch=None):
    match = re.fullmatch(r"(\d+)_(\d{8})", video.stem)
    if not match:
        raise ValueError("影片檔名必須為 公司代號_YYYYMMDD")
    code, date = match.groups()
    call_date = datetime.strptime(date, "%Y%m%d").date().isoformat()
    folder = root / "output" / video.stem
    path = folder / f"{video.stem}_metadata.json"
    metadata = read_json(path)
    raw = read_json(folder / f"{video.stem}_raw.json")
    if announcement is not None and not announcement.is_file():
        raise FileNotFoundError(announcement)
    generated = set(metadata.get("generated_fields", []))
    overrides = dict(metadata.get("manual_overrides", {}))
    # Protect direct edits made since the last generated snapshot.
    for key, previous in metadata.get("last_generated_values", {}).items():
        if metadata.get(key) != previous:
            overrides[key] = metadata.get(key)
    for key in ("company_name", "industry", "industry_code", "source_url", "audio_url", "transcript_url", "fiscal_year", "fiscal_quarter"):
        if metadata.get(key) is not None and key not in generated:
            overrides.setdefault(key, metadata[key])
    override_path = folder / f"{video.stem}_metadata_overrides.json"
    overrides.update(read_json(override_path))
    if source_url:
        overrides["source_url"] = source_url
    validate_overrides(overrides)
    warnings = []
    if offline:
        profile = read_json(root / "data/cache/company_profiles.json").get(code, {})
        warning = None if profile else "離線且無公司資料快取"
    else:
        profile, warning = company_profile(code, root / "data/cache/company_profiles.json", fetch)
    if warning:
        warnings.append(warning)
    for key in ("company_name", "industry", "industry_code"):
        metadata[key] = overrides.get(key, profile.get(key) or metadata.get(key))
        if key not in overrides:
            generated.add(key)
    metadata.update(call_id=video.stem, company_code=code, call_date=call_date)
    if profile:
        metadata["company_data_source"] = profile.get("source")
        metadata["company_data_fetched_at"] = profile.get("fetched_at")
    info = read_json(video.with_suffix(".info.json"))
    source_info = read_json(video.with_suffix(".source.json"))
    metadata["source_url"] = overrides.get("source_url") or info.get("webpage_url") or source_info.get("source_url") or metadata.get("source_url")
    if "source_url" not in overrides:
        generated.add("source_url")
    metadata.setdefault("audio_url", None)
    metadata.setdefault("transcript_url", None)
    metadata["language"] = raw.get("language") or metadata.get("language")
    wav = folder / f"{video.stem}.wav"
    if wav.exists():
        with wave.open(str(wav), "rb") as audio:
            metadata["audio_duration_sec"] = audio.getnframes() / audio.getframerate()
    else:
        metadata["audio_duration_sec"] = raw.get("duration")
    all_candidates = []
    announcement_path = announcement or video.with_suffix(".announcement.txt")
    sources = []
    if announcement_path.exists():
        all_candidates += candidates(announcement_path.read_text(encoding="utf-8-sig"), str(announcement_path))
        sources.append(str(announcement_path))
    elif search_announcements:
        kwargs = {"fetch": announcement_fetch} if announcement_fetch is not None else {}
        records, lookup_warnings = lookup(code, call_date, root / "data/cache/announcements.json", offline=offline, **kwargs)
        warnings.extend(lookup_warnings)
        for record in records:
            sources.append(record["source"])
            all_candidates += candidates(record["text"], record["source"])
    transcript = raw.get("text") or " ".join(s.get("text", "") for s in raw.get("segments", []))
    all_candidates += candidates(transcript, "transcript")
    metadata["announcement_sources"] = sorted(set(sources))
    metadata["fiscal_period_candidates"] = all_candidates
    # Automated mentions stay candidates; only human-supplied periods go to SQL.
    for key in ("fiscal_year", "fiscal_quarter"):
        metadata[key] = overrides.get(key)
        if key not in overrides:
            generated.add(key)
    pairs = {(c["fiscal_year"], c["fiscal_quarter"]) for c in all_candidates}
    if metadata.get("fiscal_year") and metadata.get("fiscal_quarter"):
        status = "manual"
    elif len(pairs) == 1:
        status = "inferred"
    elif pairs:
        status = "ambiguous"
    else:
        status = "pending"
    metadata["fiscal_period_status"] = status
    metadata.pop("fiscal_period_evidence", None)
    metadata.pop("fiscal_period_source", None)
    metadata.update(overrides)
    metadata["manual_overrides"] = overrides
    generated.difference_update(overrides)
    metadata["generated_fields"] = sorted(generated)
    metadata["last_generated_values"] = {key: metadata.get(key) for key in generated}
    metadata["pending_fields"] = [key for key in ("company_name", "industry", "fiscal_year", "fiscal_quarter") if metadata.get(key) is None]
    metadata["warnings"] = warnings
    metadata["generated_at"] = datetime.now(timezone.utc).isoformat()
    save_json(path, metadata)
    print(f"Metadata：{path}；財報期間狀態：{status}")
    if metadata["pending_fields"]:
        print("待補欄位：" + ", ".join(metadata["pending_fields"]))
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--source-url")
    parser.add_argument("--announcement", type=Path, help="法說會公告純文字檔")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    generate(args.video, args.source_url, args.announcement, offline=args.offline)


if __name__ == "__main__":
    main()
