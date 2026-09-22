"""完整流程：轉錄、清理、Metadata、QC、資料庫、講者與 QA。"""
import argparse
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from scripts.pipeline_common import ROOT, MEDIA_SUFFIXES, read_json, save_json


def find_video():
    candidates = sorted(p for p in (ROOT / "data/raw").glob("*")
                        if p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES
                        and re.fullmatch(r"\d+_\d{8}", p.stem))
    if len(candidates) != 1:
        raise ValueError("請指定影片路徑；候選檔案：" + ", ".join(p.name for p in candidates))
    return candidates[0]


def preflight():
    missing = []
    for name in ("dotenv", "aiomysql", "numpy", "torch", "pyannote.audio", "ctranslate2", "faster_whisper"):
        try:
            if importlib.util.find_spec(name) is None:
                missing.append(name)
        except (ModuleNotFoundError, ValueError):
            missing.append(name)
    if missing:
        raise RuntimeError("缺少套件：" + ", ".join(missing) + "。請先 python -m pip install -r requirements.txt")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("找不到 ffmpeg，請安裝並加入 PATH。")
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    missing = [key for key in ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME", "HF_TOKEN") if not os.getenv(key)]
    if missing:
        raise RuntimeError(".env 缺少：" + ", ".join(missing))


def build_steps(video, args):
    folder = ROOT / "output" / video.stem
    raw = folder / f"{video.stem}_raw.json"
    clean = folder / f"{video.stem}_clean.json"
    metadata = folder / f"{video.stem}_metadata.json"
    extra = []
    if args.source_url:
        extra += ["--source-url", args.source_url]
    if args.announcement:
        extra += ["--announcement", str(args.announcement.resolve())]
    if args.offline:
        extra += ["--offline"]
    force = ["--force"] if args.force else []
    return [
        ("轉錄", ["scripts/process_video.py", str(video), *force]),
        ("清理逐字稿", ["scripts/clean_transcript.py", str(raw)]),
        ("自動 Metadata", ["scripts/generate_metadata.py", str(video), *extra]),
        ("逐字稿 QC", ["scripts/transcript_qc.py", str(raw), "--metadata", str(metadata)]),
        ("匯入 MySQL", ["scripts/transcript_to_db.py", str(clean)]),
        ("講者辨識與配對", ["speaker_processor.py", video.stem, *force]),
        ("QA 分類", ["main.py", video.stem]),
    ]


def execute_steps(steps, status_path, runner=subprocess.run):
    status = {"status": "running", "started_at": datetime.now(timezone.utc).isoformat(), "steps": []}
    save_json(status_path, status)
    for index, (name, command) in enumerate(steps, 1):
        print(f"\n[{index}/{len(steps)}] {name}", flush=True)
        item = {"name": name, "status": "running"}
        status["steps"].append(item)
        save_json(status_path, status)
        try:
            code = runner([sys.executable, *command], cwd=ROOT).returncode
        except (OSError, KeyboardInterrupt) as error:
            item.update(status="failed", error=str(error))
            status["status"] = "failed"
            save_json(status_path, status)
            raise
        item.update(status="completed" if code == 0 else "review_required" if code == 2 else "failed", exit_code=code)
        save_json(status_path, status)
        if code:
            status["status"] = item["status"]
            save_json(status_path, status)
            print(f"流程停止：{name}，狀態 {item['status']}。詳見 {status_path}")
            return code
    status["status"] = "completed"
    save_json(status_path, status)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", nargs="?", type=Path)
    parser.add_argument("--source-url")
    parser.add_argument("--announcement", type=Path)
    parser.add_argument("--force", action="store_true", help="重新轉錄、重新辨識講者")
    parser.add_argument("--offline", action="store_true", help="Metadata 不連網，僅使用快取與本機資料")
    parser.add_argument("--verbose", action="store_true", help="顯示講者配對明細")
    args = parser.parse_args()
    video = args.video or find_video()
    if not video.is_absolute():
        video = ROOT / video
    if not video.is_file() or video.suffix.lower() not in MEDIA_SUFFIXES:
        raise ValueError("找不到支援的影音檔案：" + str(video))
    if not re.fullmatch(r"\d+_\d{8}", video.stem):
        raise ValueError("檔名必須為 公司代號_YYYYMMDD")
    datetime.strptime(video.stem.split("_")[1], "%Y%m%d")
    if args.announcement and not args.announcement.is_file():
        raise FileNotFoundError(args.announcement)
    folder = ROOT / "output" / video.stem
    status_path = folder / "pipeline_status.json"
    try:
        preflight()
    except Exception as error:
        save_json(status_path, {"status": "failed", "step": "preflight", "error": str(error),
                                "started_at": datetime.now(timezone.utc).isoformat()})
        raise
    if args.verbose:
        os.environ["PIPELINE_VERBOSE"] = "1"
    code = execute_steps(build_steps(video, args), status_path)
    if code:
        return code
    metadata = read_json(folder / f"{video.stem}_metadata.json")
    qc = read_json(folder / f"{video.stem}_qc_report.json")
    reasons = list(metadata.get("pending_fields", [])) + list(metadata.get("warnings", []))
    if qc.get("summary", {}).get("review_segment_count", 0):
        reasons.append("逐字稿 QC 有待核對片段")
    status = read_json(status_path)
    status.update(status="completed_with_review" if reasons else "completed", review_reasons=reasons)
    save_json(status_path, status)
    print("處理完成，仍有待確認項目：" + "; ".join(reasons) if reasons else "全部流程完成！")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as error:
        print(f"執行失敗：{error}", file=sys.stderr)
        raise SystemExit(1)
