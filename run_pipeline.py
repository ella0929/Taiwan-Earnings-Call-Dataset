import sys
import subprocess
from pathlib import Path


def run_command(command, step_name):
    """
    執行子程式。
    任一步驟失敗就停止整個 pipeline。
    """

    print()
    print("=" * 60)
    print(f"🚀 {step_name}")
    print("=" * 60)

    result = subprocess.run(command)

    if result.returncode != 0:
        print()
        print(f"❌ {step_name} 失敗")
        print("Pipeline 已停止。")
        sys.exit(result.returncode)

    print(f"✅ {step_name} 完成")


def main():

    # --------------------------------------------------
    # 檢查輸入
    # --------------------------------------------------

    if len(sys.argv) != 2:

        print("使用方法：")

        print(
            "python run_pipeline.py "
            "data/raw/2454_20260311.mp4"
        )

        return

    video_path = Path(sys.argv[1])

    if not video_path.exists():

        print(
            f"❌ 找不到影片：{video_path}"
        )

        return

    # --------------------------------------------------
    # call_id
    # --------------------------------------------------

    call_id = video_path.stem

    output_dir = (
        Path("output")
        / call_id
    )

    raw_json = (
        output_dir
        / f"{call_id}_raw.json"
    )

    clean_json = (
        output_dir
        / f"{call_id}_clean.json"
    )

    metadata_json = (
        output_dir
        / f"{call_id}_metadata.json"
    )

    print()
    print("========================================")
    print("Taiwan Earnings Call Pipeline")
    print("========================================")

    print(
        f"📌 Call ID：{call_id}"
    )

    print(
        f"📹 Video：{video_path}"
    )

    # ==================================================
    # STEP 1
    # MP4 → WAV → Whisper
    # ==================================================

    run_command(
        [
            sys.executable,
            "scripts/process_video.py",
            str(video_path)
        ],
        "Step 1：影片 → WAV → Whisper"
    )

    # --------------------------------------------------
    # 確認 Raw JSON
    # --------------------------------------------------

    if not raw_json.exists():

        print(
            f"❌ 找不到 Raw JSON："
            f"{raw_json}"
        )

        return

    # ==================================================
    # STEP 2
    # Clean Transcript
    # ==================================================

    run_command(
        [
            sys.executable,
            "scripts/clean_transcript.py",
            str(raw_json)
        ],
        "Step 2：建立 Clean Transcript"
    )

    # --------------------------------------------------
    # 確認 Clean JSON
    # --------------------------------------------------

    if not clean_json.exists():

        print(
            f"❌ 找不到 Clean JSON："
            f"{clean_json}"
        )

        return

    # ==================================================
    # STEP 3
    # Metadata check
    # ==================================================

    if not metadata_json.exists():

        print()
        print("⚠️ 尚未建立 Metadata")

        print(
            f"請建立："
            f"{metadata_json}"
        )

        print()
        print(
            "目前已完成 WAV、Raw、Clean，"
            "但暫時不寫入 MySQL。"
        )

        return

    # ==================================================
    # STEP 4
    # MySQL
    # ==================================================

    run_command(
        [
            sys.executable,
            "scripts/transcript_to_db.py",
            str(clean_json)
        ],
        "Step 3：Transcript → MySQL"
    )

    # ==================================================
    # DONE
    # ==================================================

    print()
    print("=" * 60)
    print("🎉 Pipeline 全部完成")
    print("=" * 60)

    print()
    print("輸出資料夾：")

    print(
        output_dir
    )


if __name__ == "__main__":
    main()
    