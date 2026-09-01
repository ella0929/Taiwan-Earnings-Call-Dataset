import os
import sys
import json
import wave
import asyncio

import aiomysql
import numpy as np
import torch

from pathlib import Path
from dotenv import load_dotenv
from pyannote.audio import Pipeline


BASE_DIR = Path(__file__).resolve().parent

load_dotenv(
    BASE_DIR / ".env",
    override=True
)

HF_TOKEN = os.getenv("HF_TOKEN")


def get_paths(call_id):

    output_dir = (
        BASE_DIR
        / "output"
        / call_id
    )

    audio_path = (
        output_dir
        / f"{call_id}.wav"
    )

    diarization_path = (
        output_dir
        / f"{call_id}_diarization.json"
    )

    matching_path = (
        output_dir
        / f"{call_id}_speaker_matching.json"
    )

    return (
        output_dir,
        audio_path,
        diarization_path,
        matching_path
    )


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

        audio = audio.reshape(
            -1,
            channels
        )

        audio = audio.mean(axis=1)

    waveform = (
        torch
        .from_numpy(audio)
        .unsqueeze(0)
    )

    return waveform, sample_rate


async def get_segments(call_id):

    pool = await aiomysql.create_pool(
        host=os.getenv("DB_HOST"),
        port=int(
            os.getenv(
                "DB_PORT",
                3306
            )
        ),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        db=os.getenv("DB_NAME"),
        autocommit=True
    )

    try:

        async with pool.acquire() as conn:

            async with conn.cursor() as cur:

                sql = """
                    SELECT
                        segment_id,
                        start_time_sec,
                        end_time_sec,
                        transcript_text
                    FROM speech_segments
                    WHERE call_id = %s
                    ORDER BY start_time_sec
                """

                await cur.execute(
                    sql,
                    (call_id,)
                )

                rows = await cur.fetchall()

                return [
                    {
                        "segment_id": row[0],
                        "start": (
                            float(row[1])
                            if row[1] is not None
                            else None
                        ),
                        "end": (
                            float(row[2])
                            if row[2] is not None
                            else None
                        ),
                        "text": row[3]
                    }
                    for row in rows
                ]

    finally:

        pool.close()
        await pool.wait_closed()

def run_diarization(
    audio_path
):

    if not HF_TOKEN:

        raise ValueError(
            "找不到 HF_TOKEN，請檢查 .env"
        )

    if not audio_path.exists():

        raise FileNotFoundError(
            f"找不到音檔：{audio_path}"
        )

    print(
        "音檔路徑：",
        audio_path
    )

    waveform, sample_rate = (
        load_wav(audio_path)
    )

    print(
        "Sample rate：",
        sample_rate
    )

    print(
        "Waveform shape：",
        waveform.shape
    )

    print(
        "\n開始 Speaker Diarization..."
    )

    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-community-1",
        token=HF_TOKEN
    )

    output = pipeline({
        "waveform": waveform,
        "sample_rate": sample_rate
    })

    diarization_results = []

    for (
        turn,
        speaker
    ) in output.exclusive_speaker_diarization:

        diarization_results.append({
            "speaker": speaker,
            "start": round(
                turn.start,
                2
            ),
            "end": round(
                turn.end,
                2
            )
        })

    return diarization_results


def get_diarization_results(
    output_dir,
    audio_path,
    diarization_path
):

    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    if diarization_path.exists():

        print(
            "\n找到既有 diarization JSON"
        )

        print(
            "直接讀取：",
            diarization_path
        )

        with open(
            diarization_path,
            "r",
            encoding="utf-8"
        ) as file:

            return json.load(file)

    diarization_results = (
        run_diarization(
            audio_path
        )
    )

    with open(
        diarization_path,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            diarization_results,
            file,
            ensure_ascii=False,
            indent=2
        )

    print(
        "\nDiarization 結果已存：",
        diarization_path
    )

    return diarization_results


def get_overlap(
    start1,
    end1,
    start2,
    end2
):

    return max(
        0,
        min(end1, end2)
        - max(start1, start2)
    )


def find_best_overlap_speaker(
    segment_start,
    segment_end,
    diarization_results
):

    best_speaker = None
    best_overlap = 0

    for item in diarization_results:

        overlap = get_overlap(
            segment_start,
            segment_end,
            item["start"],
            item["end"]
        )

        if overlap > best_overlap:

            best_overlap = overlap
            best_speaker = item["speaker"]

    duration = (
        segment_end
        - segment_start
    )

    overlap_ratio = (
        best_overlap / duration
        if duration > 0
        else 0
    )

    return (
        best_speaker,
        best_overlap,
        overlap_ratio
    )


def find_speaker_by_midpoint(
    segment_start,
    segment_end,
    diarization_results
):

    midpoint = (
        segment_start
        + segment_end
    ) / 2

    for item in diarization_results:

        if (
            item["start"]
            <= midpoint
            <= item["end"]
        ):

            return item["speaker"]

    return None


def create_speaker_map(
    diarization_results,
    company_code
):

    speakers = sorted(
        {
            item["speaker"]
            for item
            in diarization_results
        }
    )

    speaker_map = {}

    for index, speaker in enumerate(
        speakers,
        start=1
    ):

        speaker_map[speaker] = (
            f"{company_code}_"
            f"SPEAKER_{index:02d}"
        )

    return speaker_map


def match_segments(
    segments,
    diarization_results
):

    results = []

    for segment in segments:

        segment_id = (
            segment["segment_id"]
        )

        segment_start = (
            segment["start"]
        )

        segment_end = (
            segment["end"]
        )

        if (
            segment_start is None
            or segment_end is None
        ):

            results.append({
                "segment_id": segment_id,
                "start": segment_start,
                "end": segment_end,
                "speaker": None,
                "original_speaker": None,
                "overlap": 0,
                "overlap_ratio": 0,
                "match_method": "missing_timestamp",
                "text": segment["text"]
            })

            continue

        (
            best_speaker,
            best_overlap,
            overlap_ratio
        ) = find_best_overlap_speaker(
            segment_start,
            segment_end,
            diarization_results
        )

        if (
            best_speaker is not None
            and overlap_ratio >= 0.5
        ):

            final_speaker = (
                best_speaker
            )

            match_method = (
                "overlap"
            )

        else:

            midpoint_speaker = (
                find_speaker_by_midpoint(
                    segment_start,
                    segment_end,
                    diarization_results
                )
            )

            if midpoint_speaker is not None:

                final_speaker = (
                    midpoint_speaker
                )

                match_method = (
                    "midpoint"
                )

            elif best_speaker is not None:

                final_speaker = (
                    best_speaker
                )

                match_method = (
                    "fallback"
                )

            else:

                final_speaker = None

                match_method = (
                    "unmatched"
                )

        results.append({
            "segment_id": segment_id,
            "start": segment_start,
            "end": segment_end,
            "speaker": final_speaker,
            "original_speaker": final_speaker,
            "overlap": round(
                best_overlap,
                2
            ),
            "overlap_ratio": round(
                overlap_ratio,
                3
            ),
            "match_method": match_method,
            "text": segment["text"]
        })

    return results


def smooth_speaker_results(
    results
):

    if len(results) < 3:

        return results

    for i in range(
        1,
        len(results) - 1
    ):

        previous_speaker = (
            results[i - 1]["speaker"]
        )

        current_speaker = (
            results[i]["speaker"]
        )

        next_speaker = (
            results[i + 1]["speaker"]
        )

        if (
            previous_speaker is not None
            and previous_speaker
            == next_speaker
            and current_speaker
            != previous_speaker
            and results[i][
                "overlap_ratio"
            ] < 0.5
        ):

            results[i][
                "original_speaker"
            ] = current_speaker

            results[i][
                "speaker"
            ] = previous_speaker

            results[i][
                "match_method"
            ] = "neighbor_smoothing"

    return results


def add_database_speaker_ids(
    results,
    speaker_map
):

    for item in results:

        speaker = item["speaker"]

        if speaker is None:

            item["speaker_id"] = None

        else:

            item["speaker_id"] = (
                speaker_map.get(
                    speaker
                )
            )

    return results


def save_matching_json(
    results,
    matching_path
):

    with open(
        matching_path,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            results,
            file,
            ensure_ascii=False,
            indent=2
        )

    print(
        "\nMatching 結果已存：",
        matching_path
    )


async def write_results_to_database(
    results,
    company_code,
    call_id
):

    speaker_ids = sorted(
        {
            item["speaker_id"]
            for item in results
            if item.get(
                "speaker_id"
            ) is not None
        }
    )

    pool = await aiomysql.create_pool(
        host=os.getenv("DB_HOST"),
        port=int(
            os.getenv(
                "DB_PORT",
                3306
            )
        ),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        db=os.getenv("DB_NAME"),
        autocommit=True
    )

    try:

        async with pool.acquire() as conn:

            async with conn.cursor() as cur:

                print(
                    "\n=== 建立 / 確認 speakers ==="
                )

                for speaker_id in speaker_ids:

                    sql = """
                        INSERT INTO speakers (
                            speaker_id,
                            company_code
                        )
                        VALUES (%s, %s)
                        ON DUPLICATE KEY UPDATE
                            company_code = %s
                    """

                    await cur.execute(
                        sql,
                        (
                            speaker_id,
                            company_code,
                            company_code
                        )
                    )

                    print(
                        speaker_id,
                        "已確認"
                    )

                print(
                    "\n=== 驗證 speakers ==="
                )

                for speaker_id in speaker_ids:

                    sql = """
                        SELECT
                            speaker_id,
                            company_code
                        FROM speakers
                        WHERE speaker_id = %s
                    """

                    await cur.execute(
                        sql,
                        (speaker_id,)
                    )

                    row = (
                        await cur.fetchone()
                    )

                    if row is None:

                        raise ValueError(
                            f"{speaker_id} "
                            "沒有成功寫入 speakers"
                        )

                    print(
                        "確認存在：",
                        row[0],
                        "| company_code：",
                        row[1]
                    )

                print(
                    "\n=== 更新 speech_segments ==="
                )

                updated_count = 0
                skipped_count = 0

                for item in results:

                    segment_id = item.get(
                        "segment_id"
                    )

                    speaker_id = item.get(
                        "speaker_id"
                    )

                    if (
                        segment_id is None
                        or speaker_id is None
                    ):

                        skipped_count += 1

                        continue

                    sql = """
                        UPDATE speech_segments
                        SET
                            speaker_id = %s,
                            speaker_overlap_ratio = %s,
                            speaker_match_method = %s
                        WHERE segment_id = %s
                        AND call_id = %s
                    """

                    await cur.execute(
                        sql,
                        (
                            speaker_id,
                            item.get("overlap_ratio"),
                            item.get("match_method"),
                            segment_id,
                            call_id
                        )
                    )

                    if cur.rowcount > 0:

                        updated_count += 1

                sql = """
                    SELECT COUNT(*)
                    FROM speech_segments
                    WHERE call_id = %s
                      AND speaker_id IS NOT NULL
                """

                await cur.execute(
                    sql,
                    (call_id,)
                )

                row = (
                    await cur.fetchone()
                )

                db_matched_count = (
                    row[0]
                )

                sql = """
                    SELECT
                        speaker_id,
                        COUNT(*)
                    FROM speech_segments
                    WHERE call_id = %s
                    GROUP BY speaker_id
                    ORDER BY speaker_id
                """

                await cur.execute(
                    sql,
                    (call_id,)
                )

                speaker_counts = (
                    await cur.fetchall()
                )

                print(
                    "\n=== Database Summary ==="
                )

                print(
                    "本次真正 UPDATE：",
                    updated_count,
                    "筆"
                )

                print(
                    "跳過：",
                    skipped_count,
                    "筆"
                )

                print(
                    "DB 中 speaker_id 非 NULL：",
                    db_matched_count,
                    "/",
                    len(results)
                )

                print(
                    "\n各 speaker 筆數："
                )

                for row in speaker_counts:

                    print(
                        row[0],
                        ":",
                        row[1],
                        "筆"
                    )

                return (
                    db_matched_count
                )

    finally:

        pool.close()
        await pool.wait_closed()


async def process_speakers(
    call_id
):

    company_code = (
        call_id.split("_")[0]
    )

    (
        output_dir,
        audio_path,
        diarization_path,
        matching_path
    ) = get_paths(
        call_id
    )

    print(
        "===================================="
    )

    print(
        "Speaker Processor"
    )

    print(
        "CALL_ID：",
        call_id
    )

    print(
        "===================================="
    )

    diarization_results = (
        get_diarization_results(
            output_dir,
            audio_path,
            diarization_path
        )
    )

    print(
        "\nDiarization 區段：",
        len(diarization_results),
        "筆"
    )

    speaker_map = (
        create_speaker_map(
            diarization_results,
            company_code
        )
    )

    print(
        "\n=== Speaker Map ==="
    )

    for (
        pyannote_speaker,
        db_speaker
    ) in speaker_map.items():

        print(
            pyannote_speaker,
            "→",
            db_speaker
        )

    segments = await get_segments(
        call_id
    )

    print(
        "\nspeech_segments：",
        len(segments),
        "筆"
    )

    if not segments:

        raise ValueError(
            f"找不到 call_id={call_id} "
            "的 speech_segments"
        )

    matching_results = (
        match_segments(
            segments,
            diarization_results
        )
    )

    matching_results = (
        smooth_speaker_results(
            matching_results
        )
    )

    matching_results = (
        add_database_speaker_ids(
            matching_results,
            speaker_map
        )
    )

    method_counts = {}

    unmatched_count = 0

    for item in matching_results:

        method = (
            item["match_method"]
        )

        method_counts[method] = (
            method_counts.get(
                method,
                0
            )
            + 1
        )

        if item["speaker_id"] is None:

            unmatched_count += 1

    print(
        "\n=== Matching Summary ==="
    )

    print(
        "成功 Matching：",
        len(matching_results)
        - unmatched_count,
        "/",
        len(matching_results)
    )

    print(
        "\n=== Match Method 統計 ==="
    )

    for (
        method,
        count
    ) in method_counts.items():

        print(
            method,
            ":",
            count,
            "筆"
        )

    print(
        "\n仍無法自動判斷：",
        unmatched_count,
        "筆"
    )

    save_matching_json(
        matching_results,
        matching_path
    )

    if unmatched_count > 0:

        print(
            "\n⚠️ 有 segment 無法自動判斷，"
            "目前不寫入資料庫"
        )

        return False

    db_count = await (
        write_results_to_database(
            matching_results,
            company_code,
            call_id
        )
    )

    if db_count == len(
        matching_results
    ):

        print(
            "\n✅ Speaker Processor 全部完成"
        )

        return True

    print(
        "\n⚠️ Matching 完成，"
        "但 DB 筆數不一致"
    )

    return False


async def main():

    if len(sys.argv) < 2:

        raise ValueError(
            "請輸入 call_id，例如："
            "\npython speaker_processor.py "
            "2454_20260311"
        )

    call_id = sys.argv[1]

    await process_speakers(
        call_id
    )


if __name__ == "__main__":

    asyncio.run(
        main()
    )
