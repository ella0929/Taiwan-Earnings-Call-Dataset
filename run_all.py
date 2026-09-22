import subprocess
import sys
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
VIDEO_NAME_PATTERN = re.compile(r"^\d+_\d{8}\.(mp4|mov|mkv|avi|wav)$", re.IGNORECASE)


def run_step(command, name):
    print(f"\n{'=' * 60}\n{name}\n{'=' * 60}")
    result = subprocess.run(command, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        print(f"\n {name} 失敗，流程停止。")
        raise SystemExit(result.returncode)


def find_video():
    raw_dir = PROJECT_ROOT / "data" / "raw"
    videos = sorted(
        path for path in raw_dir.iterdir()
        if path.is_file() and VIDEO_NAME_PATTERN.match(path.name)
    ) if raw_dir.exists() else []

    if len(videos) == 1:
        return videos[0]

    if not videos:
        print(" data/raw 找不到符合格式的影片：公司代號_日期.mp4")
    else:
        print(" data/raw 找到多部符合格式的影片，請指定要處理的檔案：")
        for video in videos:
            print(f"  - {video.name}")
    print("例如：python run_all.py data/raw/2454_20260311.mp4")
    raise SystemExit(1)


def main():
    if len(sys.argv) > 2:
        print("使用方法：python run_all.py [影片路徑]")
        raise SystemExit(1)

    video_path = Path(sys.argv[1]) if len(sys.argv) == 2 else find_video()
    if not video_path.is_absolute():
        video_path = (PROJECT_ROOT / video_path).resolve()

    if not video_path.exists():
        print(f" 找不到影片：{video_path}")
        raise SystemExit(1)

    call_id = video_path.stem
    output_dir = PROJECT_ROOT / "output" / call_id
    raw_json = output_dir / f"{call_id}_raw.json"
    clean_json = output_dir / f"{call_id}_clean.json"
    metadata_json = output_dir / f"{call_id}_metadata.json"

    run_step(
        [
            sys.executable,
            str(PROJECT_ROOT / "run_pipeline.py"),
            str(video_path),
        ],
        "Step 1：完整轉錄、清理、資料庫與 Speaker 流程",
    )

    if not raw_json.exists() or not clean_json.exists() or not metadata_json.exists():
        print(" Raw JSON、Clean JSON 或 Metadata 不存在，後續流程停止。")
        raise SystemExit(1)

    run_step(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "transcript_qc.py"),
            str(raw_json),
            "--metadata",
            str(metadata_json),
        ],
        "Step 2：逐字稿品質檢查",
    )

    run_step(
        [
            sys.executable,
            str(PROJECT_ROOT / "main.py"),
            call_id,
        ],
        "Step 3：QA 分類流程",
    )

    print("\n 全部流程完成！")


if __name__ == "__main__":
    main()
