import os
import asyncio
import aiomysql
import re
import wave
from dotenv import load_dotenv

load_dotenv()
async def get_segments(call_id):

    pool = await aiomysql.create_pool(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", 3306)),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        db=os.getenv("DB_NAME")
    )

    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:

                sql = """
                    SELECT
                        segment_id,
                        transcript_text
                    FROM speech_segments
                    WHERE call_id = %s
                """

                await cur.execute(sql, (call_id,))

                rows = await cur.fetchall()

                return rows

    finally:
        pool.close()
        await pool.wait_closed()

def extract_speaker_turns(transcript_text):

    speaker_pattern = re.compile(
        r"\*\*(.+?)\s*-\s*(.+?)\*\*"
    )

    speaker_matches = list(
        speaker_pattern.finditer(transcript_text)
    )

    turns = []

    for i, match in enumerate(speaker_matches):

        speaker_name = match.group(1).strip()
        speaker_role = match.group(2).strip()

        start_pos = match.end()

        if i + 1 < len(speaker_matches):
            end_pos = speaker_matches[i + 1].start()
        else:
            end_pos = len(transcript_text)

        speaker_block = transcript_text[
            start_pos:end_pos
        ]

        time_match = re.search(
            r"\((\d{2}:\d{2}:\d{2})\)",
            speaker_block
        )

        if time_match:

            start_time_sec = time_to_seconds(
                time_match.group(1)
            )

            turns.append({
                "speaker_name": speaker_name,
                "speaker_role": speaker_role,
                "start_time_sec": start_time_sec
            })

    # 下一個人的開始時間
    # = 前一個人的結束時間
    for i in range(len(turns)):

        if i + 1 < len(turns):
            turns[i]["end_time_sec"] = (
                turns[i + 1]["start_time_sec"]
            )

        else:
            turns[i]["end_time_sec"] = None

    return turns

def create_speaker_id(company_code, speaker_name):

    clean_name = re.sub(
        r"[^A-Za-z0-9\u4e00-\u9fff]+",
        "_",
        speaker_name.strip()
    )

    clean_name = clean_name.strip("_").upper()

    return f"{company_code}_{clean_name}"

def time_to_seconds(time_string):
    hours, minutes, seconds = time_string.split(":")

    return (
        int(hours) * 3600
        + int(minutes) * 60
        + float(seconds)
    )

async def save_speaker_and_update_segment(
    cur,
    company_code,
    segment_id,
    speaker_name,
    speaker_role,
    start_time_sec,
    end_time_sec
):
    speaker_id = create_speaker_id(
        company_code,
        speaker_name
    )

    # 先確保 speaker 存在
    sql_speaker = """
        INSERT INTO speakers (
            speaker_id,
            company_code,
            speaker_name,
            speaker_role
        )
        VALUES (%s, %s, %s, %s)

        ON DUPLICATE KEY UPDATE
            speaker_name = VALUES(speaker_name),
            speaker_role = VALUES(speaker_role)
    """

    await cur.execute(
        sql_speaker,
        (
            speaker_id,
            company_code,
            speaker_name,
            speaker_role
        )
    )

    # 再更新 speech_segments
    sql_update = """
        UPDATE speech_segments
        SET
            speaker_id = %s,
            start_time_sec = %s,
            end_time_sec = %s
        WHERE segment_id = %s
    """

    await cur.execute(
        sql_update,
        (
            speaker_id,
            start_time_sec,
            end_time_sec,
            segment_id
        )
    )

    return speaker_id

def create_speaker_id(company_code, speaker_name):
    clean_name = re.sub(
        r"[^A-Za-z0-9\u4e00-\u9fff]+",
        "_",
        speaker_name.strip()
    )

    clean_name = clean_name.strip("_").upper()

    return f"{company_code}_{clean_name}"

def get_wav_duration(file_path):
    with wave.open(file_path, "rb") as audio:
        frames = audio.getnframes()
        frame_rate = audio.getframerate()

        duration = frames / float(frame_rate)

    return duration

async def main():

    call_id = "2454_2026Q2"
    company_code = call_id.split("_")[0]

    rows = await get_segments(call_id)

    # 只處理新版 turn segment
    turn_rows = [
        row for row in rows
        if "_turn_" in row[0]
    ]

    print(f"找到 {len(turn_rows)} 筆 turn segment")

    results = []

    for row in turn_rows:

        segment_id = row[0]
        transcript_text = row[1]

        turns = extract_speaker_turns(transcript_text)

        if len(turns) == 0:
            print(f"{segment_id} 找不到 speaker")
            continue

        turn = turns[0]

        results.append({
            "segment_id": segment_id,
            "speaker_name": turn["speaker_name"],
            "speaker_role": turn["speaker_role"],
            "start_time_sec": turn["start_time_sec"]
        })

    # 按開始時間排序
    results.sort(
        key=lambda x: x["start_time_sec"]
    )

    # 取得 wav 音檔總長度
    audio_path = f"{call_id}.wav"
    audio_duration = round(
    get_wav_duration(audio_path),
    2
    )

    print(f"音檔總長度：{audio_duration:.2f} 秒")

    # 下一個人的開始時間 = 前一個人的結束時間
    # 最後一個人的結束時間 = 音檔總長度
    for i in range(len(results)):

        if i + 1 < len(results):
            results[i]["end_time_sec"] = (
                results[i + 1]["start_time_sec"]
            )
        else:
            results[i]["end_time_sec"] = audio_duration

    print("\n=== Speaker Timeline ===")

    pool = await aiomysql.create_pool(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", 3306)),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        db=os.getenv("DB_NAME"),
        autocommit=True
    )

    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:

                for item in results:

                    print(
                        item["segment_id"],
                        "|",
                        item["speaker_name"],
                        "| start:",
                        item["start_time_sec"],
                        "| end:",
                        item["end_time_sec"]
                    )

                    speaker_id = await save_speaker_and_update_segment(
                        cur,
                        company_code,
                        item["segment_id"],
                        item["speaker_name"],
                        item["speaker_role"],
                        item["start_time_sec"],
                        item["end_time_sec"]
                    )

                    print(
                        f"  -> 更新成功：{speaker_id}"
                    )

    finally:
        pool.close()
        await pool.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())