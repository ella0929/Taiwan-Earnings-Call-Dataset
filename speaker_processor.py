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

async def get_transcript_segments(call_id):

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

                await cur.execute("""
                    SELECT
                        segment_id,
                        clean_text,
                        start_time_sec,
                        end_time_sec
                    FROM transcript_segments
                    WHERE call_id = %s
                    ORDER BY start_time_sec
                """, (call_id,))

                return await cur.fetchall()

    finally:
        pool.close()
        await pool.wait_closed()

def normalize_text(text):
    return " ".join(
        text.lower()
        .replace("\n", " ")
        .split()
    )

def normalize_text(text):

    text = text.lower().replace("\n", " ")

    # 修正像 "a ccording" -> "according"
    text = re.sub(
        r"\b([a-z])\s+([a-z]{2,})\b",
        r"\1\2",
        text
    )

    text = " ".join(text.split())

    return text


def match_turns_sequentially(speech_rows, transcript_rows):

    results = []

    transcript_index = 0

    for speech_row in speech_rows:

        segment_id = speech_row[0]
        speech_text = normalize_text(speech_row[1])

        matched_indexes = []

        # 只從上一個 turn 結束的位置繼續往後找
        for i in range(transcript_index, len(transcript_rows)):

            clean_text = normalize_text(transcript_rows[i][1])

            if clean_text and clean_text in speech_text:
                matched_indexes.append(i)

        if not matched_indexes:

            results.append({
                "segment_id": segment_id,
                "start_time_sec": None,
                "end_time_sec": None
            })

            continue

        first_index = matched_indexes[0]

        # 找「連續出現」的 transcript segments
        consecutive = [first_index]

        previous_index = first_index

        for index in matched_indexes[1:]:

            if index == previous_index + 1:
                consecutive.append(index)
                previous_index = index
            else:
                break

        last_index = consecutive[-1]

        start_time = float(
            transcript_rows[first_index][2]
        )

        end_time = float(
            transcript_rows[last_index][3]
        )

        results.append({
            "segment_id": segment_id,
            "start_time_sec": start_time,
            "end_time_sec": end_time
        })

        # 下一個 turn 從這裡之後開始找
        transcript_index = last_index + 1

    return results

def check_uncovered_segments(results, transcript_rows):

    uncovered = []

    for row in transcript_rows:

        segment_id = row[0]
        clean_text = row[1]
        start_time = float(row[2])
        end_time = float(row[3])

        covered = False

        for item in results:

            turn_start = item["start_time_sec"]
            turn_end = item["end_time_sec"]

            if turn_start is None or turn_end is None:
                continue

            # transcript segment 只要和某個 turn 有時間重疊
            if start_time < turn_end and end_time > turn_start:
                covered = True
                break

        if not covered:
            uncovered.append({
                "segment_id": segment_id,
                "clean_text": clean_text,
                "start": start_time,
                "end": end_time
            })

    return uncovered

async def update_segment_time(cur, segment_id, start_time_sec, end_time_sec):

    sql = """
        UPDATE speech_segments
        SET start_time_sec = %s,
            end_time_sec = %s
        WHERE segment_id = %s
    """

    await cur.execute(
        sql,
        (
            start_time_sec,
            end_time_sec,
            segment_id
        )
    )

async def main():

    call_id = "2454_20260311"

    speech_rows = await get_segments(call_id)
    transcript_rows = await get_transcript_segments(call_id)

    print(f"speech_segments：{len(speech_rows)} 筆")
    print(f"transcript_segments：{len(transcript_rows)} 筆")

    # 進行時間匹配
    results = match_turns_sequentially(
        speech_rows,
        transcript_rows
    )

    print("\n=== Sequential Time Matching ===")

    for item in results:
        print(
            item["segment_id"],
            "| start:",
            item["start_time_sec"],
            "| end:",
            item["end_time_sec"]
        )

    # 檢查是否有 transcript segment 沒有被任何 turn 涵蓋
    uncovered = check_uncovered_segments(
        results,
        transcript_rows
    )

    print("\n=== 沒有被任何 Speaker Turn 涵蓋的 transcript segments ===")
    print(f"共 {len(uncovered)} 筆")

    for item in uncovered:
        print(
            item["segment_id"],
            "|",
            item["start"],
            "→",
            item["end"],
            "|",
            item["clean_text"]
        )

    # 將匹配完成的時間寫回 speech_segments
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

                print("\n=== Update speech_segments Time ===")

                for item in results:

                    if (
                        item["start_time_sec"] is None
                        or item["end_time_sec"] is None
                    ):
                        print(
                            f'{item["segment_id"]} '
                            f'時間匹配失敗，跳過'
                        )
                        continue

                    await update_segment_time(
                        cur,
                        item["segment_id"],
                        item["start_time_sec"],
                        item["end_time_sec"]
                    )

                    print(
                        f'{item["segment_id"]} '
                        f'更新成功：'
                        f'{item["start_time_sec"]} → '
                        f'{item["end_time_sec"]}'
                    )

    finally:
        pool.close()
        await pool.wait_closed()

    print("\n✅ 所有可匹配的時間已更新完成")


if __name__ == "__main__":
    asyncio.run(main())