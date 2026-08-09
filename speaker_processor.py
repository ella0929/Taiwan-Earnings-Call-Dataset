import os
import asyncio
import aiomysql
import re
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

async def save_speaker(
    cur,
    speaker_id,
    company_code,
    speaker_name,
    speaker_role
):

    sql = """
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
        sql,
        (
            speaker_id,
            company_code,
            speaker_name,
            speaker_role
        )
    )
    
async def main():

    rows = await get_segments("2454_2026Q2")

    print(f"找到 {len(rows)} 筆 segment")

    for row in rows:

        segment_id = row[0]
        transcript_text = row[1]

        print("\n----------------------------")
        print("segment_id:", segment_id)

        turns = extract_speaker_turns(
            transcript_text
        )

        for turn in turns:

            print(
                turn["speaker_name"],
                "| start:",
                turn["start_time_sec"],
                "| end:",
                turn["end_time_sec"]
            )


if __name__ == "__main__":
    asyncio.run(main())