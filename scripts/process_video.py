import json
import subprocess
import sys
from pathlib import Path

import whisper


# =========================
# 設定
# =========================

MODEL_NAME = "small"

SAMPLE_RATE = 16000
CHANNELS = 1


# =========================
# MP4 → WAV
# =========================

def convert_to_wav(video_path: Path, wav_path: Path):

    print("\n🎵 [1/4] MP4 → WAV")

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-ar",
        str(SAMPLE_RATE),
        "-ac",
        str(CHANNELS),
        str(wav_path)
    ]

    subprocess.run(command, check=True)

    print(f"✅ WAV 完成：{wav_path}")


# =========================
# Whisper
# =========================

def transcribe_audio(wav_path: Path, raw_json_path: Path):

    print("\n🎤 [2/4] Whisper 自動轉錄")

    print(f"載入 Whisper 模型：{MODEL_NAME}")

    model = whisper.load_model(MODEL_NAME)

    result = model.transcribe(
        str(wav_path),

        task="transcribe",

        fp16=False,

        verbose=True,


        word_timestamps=True
    )

    with open(
        raw_json_path,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            result,
            f,
            ensure_ascii=False,
            indent=2
        )

    print(f"✅ Raw JSON 完成：{raw_json_path}")

    return result


# =========================
# 建立 TXT
# =========================

def save_raw_txt(result, txt_path: Path):

    text = result.get("text", "").strip()

    with open(
        txt_path,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(text)

    print(f"✅ Raw TXT 完成：{txt_path}")


# =========================
# 主程式
# =========================

def process_video(video_file):

    video_path = Path(video_file)

    if not video_path.exists():

        print(f"❌ 找不到影片：{video_path}")

        return

    if video_path.suffix.lower() != ".mp4":

        print("❌ 目前只接受 MP4")

        return

    call_id = video_path.stem

    output_dir = Path("output") / call_id

    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    wav_path = (
        output_dir /
        f"{call_id}.wav"
    )

    raw_json_path = (
        output_dir /
        f"{call_id}_raw.json"
    )

    raw_txt_path = (
        output_dir /
        f"{call_id}_raw.txt"
    )

    # -------------------------
    # Step 1
    # -------------------------

    if not wav_path.exists():

        convert_to_wav(
            video_path,
            wav_path
        )

    else:

        print(
            f"\n⏭️ WAV 已存在，跳過：{wav_path}"
        )

    # -------------------------
    # Step 2
    # -------------------------

    if not raw_json_path.exists():

        result = transcribe_audio(
            wav_path,
            raw_json_path
        )

    else:

        print(
            f"\n⏭️ Raw JSON 已存在，直接讀取"
        )

        with open(
            raw_json_path,
            "r",
            encoding="utf-8"
        ) as f:

            result = json.load(f)

    # -------------------------
    # Step 3
    # -------------------------

    save_raw_txt(
        result,
        raw_txt_path
    )

    print("\n🎉 自動轉錄完成！")

    print("\n輸出：")

    for file in output_dir.iterdir():

        print(
            f"  - {file.name}"
        )


# =========================
# Entry
# =========================

if __name__ == "__main__":

    if len(sys.argv) != 2:

        print(
            "\n使用方式："
        )

        print(
            "python scripts/process_video.py "
            "data/raw/2454_20260311.mp4"
        )

        sys.exit(1)

    process_video(
        sys.argv[1]
    )
    