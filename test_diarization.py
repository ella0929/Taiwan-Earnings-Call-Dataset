import os
import wave
import numpy as np
import torch

from pathlib import Path
from dotenv import load_dotenv
from pyannote.audio import Pipeline


env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(env_path, override=True)

HF_TOKEN = os.getenv("HF_TOKEN")

print("HF_TOKEN 有讀到：", bool(HF_TOKEN))

if not HF_TOKEN:
    raise ValueError("找不到 HF_TOKEN，請檢查 .env")


audio_path = (
    Path(__file__).resolve().parent
    / "output"
    / "2454_20260311"
    / "2454_20260311.wav"
)

print("音檔路徑：", audio_path)
print("音檔存在：", audio_path.exists())


def load_wav(path):
    with wave.open(str(path), "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        frames = wav_file.readframes(
            wav_file.getnframes()
        )

    if sample_width == 2:
        audio = np.frombuffer(
            frames,
            dtype=np.int16
        ).astype(np.float32)

        audio /= 32768.0

    elif sample_width == 4:
        audio = np.frombuffer(
            frames,
            dtype=np.int32
        ).astype(np.float32)

        audio /= 2147483648.0

    else:
        raise ValueError(
            f"目前不支援 sample width: {sample_width}"
        )

    if channels > 1:
        audio = audio.reshape(-1, channels)
        audio = audio.mean(axis=1)

    waveform = torch.from_numpy(audio).unsqueeze(0)

    return waveform, sample_rate


waveform, sample_rate = load_wav(audio_path)

print("Sample rate：", sample_rate)
print("Waveform shape：", waveform.shape)


pipeline = Pipeline.from_pretrained(
    "pyannote/speaker-diarization-community-1",
    token=HF_TOKEN
)

# 先只測前 60 秒
test_waveform = waveform[:, :sample_rate * 60]

print("開始分析前 60 秒...")

output = pipeline({
    "waveform": test_waveform,
    "sample_rate": sample_rate
})

print("\n=== Speaker Diarization ===")

for turn, speaker in output.speaker_diarization:
    print(
        speaker,
        "|",
        round(turn.start, 2),
        "→",
        round(turn.end, 2)
    )