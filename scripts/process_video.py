import json
import os
import subprocess
import sys
from pathlib import Path

import ctranslate2
from faster_whisper import WhisperModel

from dotenv import load_dotenv
try:
    from .pipeline_common import ROOT, MEDIA_SUFFIXES, fingerprint, cache_matches, save_cache, save_json
except ImportError:
    from pipeline_common import ROOT, MEDIA_SUFFIXES, fingerprint, cache_matches, save_cache, save_json

load_dotenv(ROOT / ".env")

# =========================
# 設定
# =========================

MODEL_NAME = os.getenv("WHISPER_MODEL", "large-v3")
DEVICE = os.getenv("WHISPER_DEVICE", "auto").lower()
COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "auto").lower()
LANGUAGE = os.getenv("WHISPER_LANGUAGE") or None
HOTWORDS = os.getenv("WHISPER_HOTWORDS") or None
BEAM_SIZE = int(os.getenv("WHISPER_BEAM_SIZE", "5"))

SAMPLE_RATE = 16000
CHANNELS = 1

# =========================
# MP4 → WAV
# =========================

def convert_to_wav(video_path: Path, wav_path: Path):

    print("\n🎵 影音 → WAV")

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i",
        str(video_path),
        "-ar",
        str(SAMPLE_RATE),
        "-ac",
        str(CHANNELS),
        "-vn",
        "-c:a", "pcm_s16le",
        str(wav_path.with_suffix(".tmp.wav"))
    ]

    subprocess.run(command, check=True)
    wav_path.with_suffix(".tmp.wav").replace(wav_path)

    print(f"✅ WAV 完成：{wav_path}")

# =========================
# Faster-Whisper
# =========================

def resolve_runtime():
    """選擇 Faster-Whisper 的執行裝置與數值精度。"""

    if DEVICE not in {"auto", "cpu", "cuda"}:
        raise ValueError(
            "WHISPER_DEVICE 必須是 auto、cpu 或 cuda"
        )

    device = DEVICE

    if device == "auto":
        device = ( "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu" )

    compute_type = COMPUTE_TYPE

    if compute_type == "auto":
        compute_type = "int8"

    return device, compute_type

def build_transcription_config(device, compute_type):
    return {
        "engine": "faster-whisper",
        "model": MODEL_NAME,
        "device": device,
        "compute_type": compute_type,
        "language": LANGUAGE,
        "beam_size": BEAM_SIZE,
        "word_timestamps": True,
        # 為了保留輕聲 filler，Raw pass 預設不使用 VAD 刪除片段。
        "vad_filter": False,
        "hotwords": HOTWORDS
    }

def transcribe_audio(
    wav_path: Path,
    raw_json_path: Path,
    device,
    compute_type
):

    print("\n🎤 Faster-Whisper 自動轉錄")

    print(f"模型：{MODEL_NAME}")
    print(f"裝置：{device}")
    print(f"運算精度：{compute_type}")

    model = WhisperModel( MODEL_NAME, device=device, compute_type=compute_type )

    segment_generator, info = model.transcribe(
        str(wav_path),
        task="transcribe",
        language=LANGUAGE,
        beam_size=BEAM_SIZE,
        word_timestamps=True,
        vad_filter=False,
        condition_on_previous_text=True,
        hotwords=HOTWORDS
    )

    segments = []
    text_parts = []
    duration = float(info.duration or 0)

    # Faster-Whisper 回傳 generator；實際轉錄在迭代時才執行。
    for segment in segment_generator:
        words = []

        for word_info in segment.words or []:
            words.append({
                "word": word_info.word,
                "start": word_info.start,
                "end": word_info.end,
                "probability": word_info.probability
            })

        segment_data = {
            "id": segment.id,
            "seek": segment.seek,
            "start": segment.start,
            "end": segment.end,
            "text": segment.text,
            "tokens": list(segment.tokens),
            "temperature": segment.temperature,
            "avg_logprob": segment.avg_logprob,
            "compression_ratio": segment.compression_ratio,
            "no_speech_prob": segment.no_speech_prob,
            "words": words
        }

        segments.append(segment_data)
        text_parts.append(segment.text)

        progress = ( min(segment.end / duration * 100, 100) if duration > 0 else 0 )

        print(
            f"\r⏳ 轉錄進度：{progress:6.2f}% "
            f"({segment.end:.1f}/{duration:.1f} 秒)",
            end="",
            flush=True
        )

    print()

    result = {
        "text": "".join(text_parts).strip(),
        "segments": segments,
        "language": info.language,
        "language_probability": info.language_probability,
        "duration": info.duration,
        "duration_after_vad": getattr(
            info,
            "duration_after_vad",
            None
        ),
        "transcription_config": build_transcription_config(
            device,
            compute_type
        )
    }

    temporary_path = raw_json_path.with_suffix(".json.tmp")

    with open(
        temporary_path,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump( result, f, ensure_ascii=False, indent=2 )

    temporary_path.replace(raw_json_path)

    print(f"✅ Raw JSON 完成：{raw_json_path}")

    return result

# =========================
# 建立 TXT
# =========================

def format_timestamp(seconds: float) -> str:
    total_ms = round(float(seconds) * 1000)

    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1000)

    return ( f"{hours:02d}:{minutes:02d}:" f"{secs:02d}.{milliseconds:03d}" )

def save_raw_txt(result, txt_path: Path):
    segments = result.get("segments", [])

    with open(txt_path, "w", encoding="utf-8") as file:
        for segment in segments:
            start = format_timestamp(segment.get("start", 0))
            end = format_timestamp(segment.get("end", 0))
            text = segment.get("text", "").strip()

            if text:
                file.write(f"[{start} --> {end}] {text}\n")

    print(f"✅ Raw TXT 完成：{txt_path}")

# =========================
# 主程式
# =========================

def process_video(video_file, force=False):
    video_path = Path(video_file).resolve()
    if not video_path.is_file():
        raise FileNotFoundError(video_path)
    if video_path.suffix.lower() not in MEDIA_SUFFIXES:
        raise ValueError("支援格式：MP4、MOV、MKV、AVI、WAV")
    output_dir = ROOT / "output" / video_path.stem
    output_dir.mkdir(parents=True, exist_ok=True)
    wav = output_dir / f"{video_path.stem}.wav"
    raw = output_dir / f"{video_path.stem}_raw.json"
    if wav.resolve() == video_path:
        raise ValueError("請將來源音檔放在 data/raw，避免覆寫來源檔案")
    wav_manifest = wav.with_suffix(".cache.json")
    inputs = {"source_sha256": fingerprint(video_path), "sample_rate": SAMPLE_RATE,
              "channels": CHANNELS, "codec": "pcm_s16le", "version": 1}
    if force or not cache_matches(wav, wav_manifest, inputs):
        convert_to_wav(video_path, wav)
        save_cache(wav, wav_manifest, inputs)
    else:
        print("沿用已驗證來源的 WAV")
    device, compute_type = resolve_runtime()
    config = build_transcription_config(device, compute_type)
    raw_inputs = {"audio_sha256": fingerprint(wav), "config": config, "version": 1}
    manifest = raw.with_suffix(".cache.json")
    if force or not cache_matches(raw, manifest, raw_inputs):
        result = transcribe_audio(wav, raw, device, compute_type)
        result["source_sha256"] = inputs["source_sha256"]
        save_json(raw, result)
        save_cache(raw, manifest, raw_inputs)
    else:
        result = json.loads(raw.read_text(encoding="utf-8"))
        print("沿用已驗證音檔與設定的轉錄")
    if not result.get("segments"):
        raise RuntimeError("轉錄沒有任何片段，停止後續匯入")
    save_raw_txt(result, output_dir / f"{video_path.stem}_raw.txt")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("video")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    process_video(args.video, args.force)
