import os
import sys
import json
import re
import asyncio
import wave

from pathlib import Path
from datetime import datetime

import aiomysql
from dotenv import load_dotenv


# ============================================================
# 1. 專案位置與 .env
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

ENV_PATH = PROJECT_ROOT / ".env"

load_dotenv(ENV_PATH)


# ============================================================
# 2. MySQL Connection Pool
# ============================================================

async def create_pool():

    required_vars = [
        "DB_HOST",
        "DB_USER",
        "DB_PASSWORD",
        "DB_NAME"
    ]

    missing = [
        var
        for var in required_vars
        if not os.getenv(var)
    ]

    if missing:
        raise RuntimeError(
            "缺少 .env 設定：" + ", ".join(missing)
        )

    return await aiomysql.create_pool(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", 3306)),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        db=os.getenv("DB_NAME"),
        autocommit=False
    )


# ============================================================
# 3. 讀 JSON
# ============================================================

def load_json(path: Path):

    with open(
        path,
        "r",
        encoding="utf-8"
    ) as f:

        return json.load(f)


# ============================================================
# 4. 解析 call_id
# ============================================================

def parse_call_id(call_id: str):
    """
    命名規則：

    2454_20260311

    →
    company_code = 2454
    call_date = 2026-03-11
    fiscal_year = 2026
    fiscal_quarter = Q1
    """

    pattern = r"^(\d+)_(\d{8})$"

    match = re.match(
        pattern,
        call_id
    )

    if not match:

        raise ValueError(
            f"call_id 格式錯誤：{call_id}\n"
            f"預期格式例如：2454_20260311"
        )

    company_code = match.group(1)

    date_string = match.group(2)

    parsed_date = datetime.strptime(
        date_string,
        "%Y%m%d"
    )

    fiscal_year = parsed_date.year

    call_date = parsed_date.strftime(
        "%Y-%m-%d"
    )

    month = parsed_date.month

    if month <= 3:
        fiscal_quarter = "Q1"

    elif month <= 6:
        fiscal_quarter = "Q2"

    elif month <= 9:
        fiscal_quarter = "Q3"

    else:
        fiscal_quarter = "Q4"

    return {
        "company_code": company_code,
        "fiscal_year": fiscal_year,
        "fiscal_quarter": fiscal_quarter,
        "call_date": call_date
    }


# ============================================================
# 5. WAV 長度
# ============================================================

def get_wav_duration(wav_path: Path):

    if not wav_path.exists():
        return None

    try:

        with wave.open(
            str(wav_path),
            "rb"
        ) as audio:

            frames = audio.getnframes()

            frame_rate = audio.getframerate()

            if frame_rate == 0:
                return None

            return round(
                frames / float(frame_rate),
                2
            )

    except Exception as e:

        print(
            f"⚠️ 無法讀取 WAV 長度：{e}"
        )

        return None


# ============================================================
# 6. 檢查 earnings_calls 是否已存在
# ============================================================

async def earnings_call_exists(
    cur,
    call_id
):

    sql = """
        SELECT call_id
        FROM earnings_calls
        WHERE call_id = %s
        LIMIT 1
    """

    await cur.execute(
        sql,
        (call_id,)
    )

    row = await cur.fetchone()

    return row is not None


# ============================================================
# 7. 建立 / 更新 earnings_calls
# ============================================================

async def ensure_earnings_call(
    cur,
    call_id,
    language,
    metadata_path,
    wav_path
):

    parsed = parse_call_id(
        call_id
    )

    exists = await earnings_call_exists(
        cur,
        call_id
    )

    # --------------------------------------------------------
    # 如果已經有 earnings_call
    # --------------------------------------------------------

    if exists:

        sql = """
            UPDATE earnings_calls
            SET
                language = COALESCE(%s, language),
                audio_duration_sec =
                    COALESCE(%s, audio_duration_sec),
                updated_at = CURRENT_TIMESTAMP
            WHERE call_id = %s
        """

        audio_duration = get_wav_duration(
            wav_path
        )

        await cur.execute(
            sql,
            (
                language,
                audio_duration,
                call_id
            )
        )

        print(
            f"✅ earnings_calls 已存在：{call_id}"
        )

        return

    # --------------------------------------------------------
    # 如果不存在，metadata 必須存在
    # --------------------------------------------------------

    if not metadata_path.exists():

        raise FileNotFoundError(
            "\n找不到 metadata.json。\n"
            f"請建立：{metadata_path}\n"
            "因為 earnings_calls 的 company_name "
            "是 NOT NULL，不能自動亂填。"
        )

    metadata = load_json(
        metadata_path
    )

    company_name = metadata.get(
        "company_name"
    )

    if not company_name:

        raise ValueError(
            "metadata.json 缺少 company_name"
        )

    industry = metadata.get(
        "industry"
    )

    fiscal_quarter = metadata.get(
        "fiscal_quarter"
    ) or parsed["fiscal_quarter"]

    source_url = metadata.get(
        "source_url"
    )

    audio_url = metadata.get(
        "audio_url"
    )

    transcript_url = metadata.get(
        "transcript_url"
    )

    audio_duration = get_wav_duration(
        wav_path
    )

    sql = """
        INSERT INTO earnings_calls
        (
            call_id,
            company_code,
            company_name,
            industry,
            fiscal_year,
            fiscal_quarter,
            call_date,
            language,
            audio_url,
            transcript_url,
            source_url,
            audio_duration_sec
        )

        VALUES
        (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s
        )
    """

    await cur.execute(
        sql,
        (
            call_id,
            parsed["company_code"],
            company_name,
            industry,
            parsed["fiscal_year"],
            fiscal_quarter,
            parsed["call_date"],
            language,
            audio_url,
            transcript_url,
            source_url,
            audio_duration
        )
    )

    print(
        f"✅ 新增 earnings_calls：{call_id}"
    )


# ============================================================
# 8. 清除舊 preprocessing 資料
# ============================================================

async def clear_old_transcript_data(
    cur,
    call_id
):

    """
    transcript_segments / transcript_words
    屬於 preprocessing layer。

    同一場重新跑 Whisper / Clean 時，
    可以整批重新建立。

    不影響：
    - speech_segments
    - speakers
    - segment_features
    - confidence_results
    """

    # 先刪 child table
    await cur.execute(
        """
        DELETE FROM transcript_words
        WHERE call_id = %s
        """,
        (call_id,)
    )

    # 再刪 parent table
    await cur.execute(
        """
        DELETE FROM transcript_segments
        WHERE call_id = %s
        """,
        (call_id,)
    )

    print(
        "🧹 已清除該場舊的 transcript preprocessing 資料"
    )


# ============================================================
# 9. 寫 transcript_segments
# ============================================================

async def insert_segment(
    cur,
    call_id,
    segment,
    fallback_index
):

    whisper_segment_id = segment.get(
        "id"
    )

    if whisper_segment_id is None:
        whisper_segment_id = fallback_index

    segment_id = (
        f"{call_id}_seg_"
        f"{int(whisper_segment_id):04d}"
    )

    start_time = segment.get(
        "start"
    )

    end_time = segment.get(
        "end"
    )

    if start_time is not None:
        start_time = round(float(start_time), 2)

    if end_time is not None:
        end_time = round(float(end_time), 2)

    raw_text = segment.get(
        "original_text",
        ""
    )

    clean_text = segment.get(
        "clean_text",
        ""
    )

    sql = """
        INSERT INTO transcript_segments
        (
            segment_id,
            call_id,
            whisper_segment_id,
            start_time_sec,
            end_time_sec,
            raw_text,
            clean_text
        )

        VALUES
        (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s
        )
    """

    await cur.execute(
        sql,
        (
            segment_id,
            call_id,
            whisper_segment_id,
            start_time,
            end_time,
            raw_text,
            clean_text
        )
    )

    return segment_id


# ============================================================
# 10. 寫 transcript_words
# ============================================================

async def insert_words(
    cur,
    call_id,
    segment_id,
    words
):

    if not words:
        return 0

    sql = """
        INSERT INTO transcript_words
        (
            segment_id,
            call_id,
            word_index,
            word,
            start_time_sec,
            end_time_sec,
            removed_as_filler,
            removed_reason
        )

        VALUES
        (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s
        )
    """

    rows = []

    for index, word_info in enumerate(words):

        word = word_info.get(
            "word",
            ""
        )

        start = word_info.get(
            "start"
        )

        end = word_info.get(
            "end"
        )

        if start is not None:
            start = round(float(start), 2)

        if end is not None:
            end = round(float(end), 2)

        removed = bool(
            word_info.get(
                "removed",
                False
            )
        )

        removed_reason = (
            "filler"
            if removed
            else None
        )

        rows.append(
            (
                segment_id,
                call_id,
                index,
                word,
                start,
                end,
                removed,
                removed_reason
            )
        )

    await cur.executemany(
        sql,
        rows
    )

    return len(rows)


# ============================================================
# 11. 主要匯入程序
# ============================================================

async def import_transcript(
    clean_json_path: Path,
    metadata_path: Path
):

    if not clean_json_path.exists():

        raise FileNotFoundError(
            f"找不到 Clean JSON："
            f"{clean_json_path}"
        )

    # 2454_20260311_clean.json
    # →
    # 2454_20260311
    call_id = (
        clean_json_path
        .stem
        .replace(
            "_clean",
            ""
        )
    )

    data = load_json(
        clean_json_path
    )

    language = data.get(
        "language"
    )

    segments = data.get(
        "segments",
        []
    )

    wav_path = (
        clean_json_path.parent
        / f"{call_id}.wav"
    )

    print()
    print(
        "======================================"
    )
    print(
        "Transcript → MySQL"
    )
    print(
        "======================================"
    )

    print(
        f"📌 call_id：{call_id}"
    )

    print(
        f"🌐 language：{language}"
    )

    print(
        f"🔍 segments：{len(segments)}"
    )

    print(
        f"📂 Clean JSON：{clean_json_path}"
    )

    print(
        f"📂 Metadata：{metadata_path}"
    )

    pool = None

    try:

        print()
        print(
            "🔌 正在連接 MySQL..."
        )

        pool = await create_pool()

        async with pool.acquire() as conn:

            try:

                async with conn.cursor() as cur:

                    # ----------------------------------------
                    # Earnings Call
                    # ----------------------------------------

                    await ensure_earnings_call(
                        cur,
                        call_id,
                        language,
                        metadata_path,
                        wav_path
                    )

                    # ----------------------------------------
                    # 清掉舊 preprocessing
                    # ----------------------------------------

                    await clear_old_transcript_data(
                        cur,
                        call_id
                    )

                    segment_count = 0
                    word_count = 0

                    # ----------------------------------------
                    # 寫入 Segments + Words
                    # ----------------------------------------

                    for index, segment in enumerate(
                        segments
                    ):

                        segment_id = (
                            await insert_segment(
                                cur,
                                call_id,
                                segment,
                                index
                            )
                        )

                        segment_count += 1

                        words = segment.get(
                            "words",
                            []
                        )

                        inserted_word_count = (
                            await insert_words(
                                cur,
                                call_id,
                                segment_id,
                                words
                            )
                        )

                        word_count += (
                            inserted_word_count
                        )

                        if (
                            (index + 1) % 20 == 0
                            or (index + 1) == len(segments)
                        ):
                            print(
                                f"⏳ 已處理 "
                                f"{index + 1}/{len(segments)} "
                                f"個 segments，"
                                f"{word_count} 個 words",
                                flush=True
                            )

                    # ----------------------------------------
                    # 全部成功才 Commit
                    # ----------------------------------------

                    await conn.commit()

                    print()
                    print(
                        "======================================"
                    )
                    print(
                        "✅ MySQL 寫入成功"
                    )
                    print(
                        "======================================"
                    )

                    print(
                        f"Transcript Segments："
                        f"{segment_count}"
                    )

                    print(
                        f"Transcript Words："
                        f"{word_count}"
                    )

            except Exception:

                await conn.rollback()

                raise

    finally:

        if pool is not None:

            pool.close()

            await pool.wait_closed()


# ============================================================
# 12. Entry
# ============================================================

async def main():

    if len(sys.argv) < 2:

        print()
        print(
            "使用方法："
        )

        print(
            "python scripts/transcript_to_db.py "
            "output/2454_20260311/"
            "2454_20260311_clean.json"
        )

        return

    clean_json_path = Path(
        sys.argv[1]
    )

    call_id = (
        clean_json_path
        .stem
        .replace(
            "_clean",
            ""
        )
    )

    # 如果沒有額外指定 metadata
    # 自動找同一個資料夾
    if len(sys.argv) >= 3:

        metadata_path = Path(
            sys.argv[2]
        )

    else:

        metadata_path = (
            clean_json_path.parent
            / f"{call_id}_metadata.json"
        )

    try:

        await import_transcript(
            clean_json_path,
            metadata_path
        )

    except Exception as e:

        print()
        print(
            "❌ Transcript → MySQL 失敗"
        )

        print(
            f"錯誤：{e}"
        )

        raise


if __name__ == "__main__":

    asyncio.run(
        main()
    )
    