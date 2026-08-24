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
    speaker_id,
    start_time_sec,
    end_time_sec
):
    # 先確保 speaker_id 存在 speakers
    sql_speaker = """
        INSERT IGNORE INTO speakers (
            speaker_id,
            company_code
        )
        VALUES (%s, %s)
    """

    await cur.execute(
        sql_speaker,
        (
            speaker_id,
            company_code
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

def create_anonymous_speaker_id(
    company_code,
    speaker_number
):
    return (
        f"{company_code}_"
        f"SPEAKER_{speaker_number:02d}"
    )

def assign_anonymous_speaker_ids(
    results,
    company_code
):
    speaker_map = {}
    speaker_counter = 1

    for item in results:

        # 真實 speaker 名稱只拿來當內部 matching key
        speaker_key = item["speaker_name"]

        if speaker_key not in speaker_map:

            speaker_map[speaker_key] = (
                create_anonymous_speaker_id(
                    company_code,
                    speaker_counter
                )
            )

            speaker_counter += 1

        item["speaker_id"] = (
            speaker_map[speaker_key]
        )

    return results

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

    # 從 call_id 取得公司代碼
    # 2454_20260311 -> 2454
    company_code = call_id.split("_")[0]

    # 取得 speech_segments
    speech_rows = await get_segments(call_id)

    # 取得 transcript_segments
    transcript_rows = await get_transcript_segments(call_id)

    print(f"speech_segments：{len(speech_rows)} 筆")
    print(f"transcript_segments：{len(transcript_rows)} 筆")

    # ==================================================
    # 1. 從 speech_segments 找 Speaker + 開始時間
    # ==================================================

    results = []

    for row in speech_rows:

        segment_id = row[0]
        transcript_text = row[1]

        turns = extract_speaker_turns(
            transcript_text
        )

        # 如果這筆沒有 speaker，就跳過
        if len(turns) == 0:
            print(
                f"{segment_id} 找不到 speaker，跳過"
            )
            continue

        # 現在一個 segment 應該只對應一個 speaker turn
        turn = turns[0]

        results.append({
            "segment_id": segment_id,

            # speaker_name 只在程式內部拿來判斷
            # 是否為同一位 speaker
            "speaker_name": turn["speaker_name"],

            "start_time_sec": turn["start_time_sec"]
        })

    # ==================================================
    # 2. 按時間排序
    # ==================================================

    results.sort(
        key=lambda x: x["start_time_sec"]
    )

    # ==================================================
    # 3. 將真實 speaker 標記轉成匿名 speaker_id
    #
    # 例如：
    # A -> 2454_SPEAKER_01
    # B -> 2454_SPEAKER_02
    # A -> 2454_SPEAKER_01
    # ==================================================

    results = assign_anonymous_speaker_ids(
        results,
        company_code
    )

    # ==================================================
    # 4. 計算 end_time_sec
    #
    # 下一段 start = 前一段 end
    # 最後一段 end = WAV 音檔總長度
    # ==================================================

    audio_path = f"{call_id}.wav"

    if os.path.exists(audio_path):

        audio_duration = round(
            get_wav_duration(audio_path),
            2
        )

        print(
            f"音檔總長度：{audio_duration:.2f} 秒"
        )

    else:

        audio_duration = None

        print(
            f"⚠️ 找不到音檔：{audio_path}"
        )

    for i in range(len(results)):

        # 不是最後一筆
        if i + 1 < len(results):

            results[i]["end_time_sec"] = (
                results[i + 1]["start_time_sec"]
            )

        # 最後一筆
        else:

            results[i]["end_time_sec"] = (
                audio_duration
            )

    # ==================================================
    # 5. 顯示 Matching 結果
    # ==================================================

    print("\n=== Sequential Time Matching ===")

    for item in results:

        print(
            item["segment_id"],
            "|",
            item["speaker_id"],
            "| start:",
            item["start_time_sec"],
            "| end:",
            item["end_time_sec"]
        )

    # ==================================================
    # 6. 檢查 transcript_segments
    #    是否有沒有被 Speaker Turn 涵蓋的資料
    # ==================================================

    uncovered = check_uncovered_segments(
        results,
        transcript_rows
    )

    print(
        "\n=== 沒有被任何 Speaker Turn 涵蓋的 "
        "transcript segments ==="
    )

    print(
        f"共 {len(uncovered)} 筆"
    )

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

    # ==================================================
    # 7. 寫入資料庫
    # ==================================================

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
                    "\n=== Update speech_segments ==="
                )

                for item in results:

                    # 如果時間不完整就不寫
                    if (
                        item["start_time_sec"] is None
                        or item["end_time_sec"] is None
                    ):

                        print(
                            f'{item["segment_id"]} '
                            f'時間匹配失敗，跳過'
                        )

                        continue

                    await save_speaker_and_update_segment(
                        cur=cur,
                        company_code=company_code,
                        segment_id=item["segment_id"],
                        speaker_id=item["speaker_id"],
                        start_time_sec=item[
                            "start_time_sec"
                        ],
                        end_time_sec=item[
                            "end_time_sec"
                        ]
                    )

                    print(
                        f'{item["segment_id"]} '
                        f'更新成功：'
                        f'{item["speaker_id"]} | '
                        f'{item["start_time_sec"]} '
                        f'→ '
                        f'{item["end_time_sec"]}'
                    )

    finally:

        pool.close()
        await pool.wait_closed()

    print(
        "\n✅ 所有可匹配的 Speaker "
        "與時間已更新完成"
    )


if __name__ == "__main__":
    asyncio.run(main())