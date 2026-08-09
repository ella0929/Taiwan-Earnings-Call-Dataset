import os
import re
import asyncio
import aiomysql
from dotenv import load_dotenv

load_dotenv()

def parse_txt_file(file_path: str):
    """
    通用型逐字稿解析器：相容台積電、聯發科等不同公司的中英文法說會逐字稿格式
    """
    file_name = os.path.basename(file_path)
    call_id = os.path.splitext(file_name)[0]

    with open(file_path, "r", encoding="utf-8") as f:
        transcript_text = f.read()

    # 1. 中英文 Q&A 區段開頭關鍵字（擴充相容性）
    qa_keywords = [
        r"Question-and-Answer Session", r"Questions and Answers", r"Q&A", r"q&a", 
        r"Q and A", r"問答", r"提問", r"問與答", r"開放提問", r"現場提問"
    ]
    qa_start_pos = -1

    for kw in qa_keywords:
        match = re.search(kw, transcript_text, re.IGNORECASE)
        if match:
            qa_start_pos = match.start()
            break

    segments = []

    # 2. 拆分簡報與 Q&A
    if qa_start_pos != -1:
        pres_text = transcript_text[:qa_start_pos].strip()
        qa_text = transcript_text[qa_start_pos:].strip()

        if pres_text:
            segments.append({
                "segment_id": f"{call_id}_pres_01",
                "call_id": call_id,
                "section_type": "presentation",
                "question_id": None,
                "transcript_text": pres_text,
                "word_count": len(pres_text)
            })
    else:
        qa_text = transcript_text.strip()

    # 3. 解析 Speaker Turn（擴充萬用講者切換標頭）
    if qa_text:
        # 強大模式：支援 **人名**、英文名字 (C.C. Wei:)、中文姓名職務 (魏哲家總裁：)、Q1/A1、Operator 等
        speaker_pattern = r'(\n(?:\*\*[^*]+\*\*|[A-Z][a-zA-Z\.\s]+[：:]|[\u4e00-\u9fa5]{2,4}(?:董事長|總裁|副總|分析師|提問)?[\s：:]|Q\d*|A\d*|Operator|提問|回答)[：:]?)'
        
        raw_blocks = re.split(speaker_pattern, qa_text)

        turn_count = 0
        current_header = ""
        current_text = ""

        for block in raw_blocks:
            text = block.strip()
            if not text:
                continue

            # 驗證此區塊是否為講者標頭
            is_header = re.match(r'^(\*\*[^*]+\*\*|[A-Z][a-zA-Z\.\s]+[：:]?|[\u4e00-\u9fa5]{2,4}(?:董事長|總裁|副總|分析師|提問)?[\s：:]?|Q\d*|A\d*|Operator|提問|回答)[：:]?$', text, re.IGNORECASE)

            if is_header:
                if current_text.strip():
                    turn_count += 1
                    seg_id = f"{call_id}_turn_{turn_count:02d}"
                    segments.append({
                        "segment_id": seg_id,
                        "call_id": call_id,
                        "section_type": "qa",
                        "question_id": f"Q_{turn_count:02d}",
                        "transcript_text": f"{current_header} {current_text}".strip(),
                        "word_count": len(current_text.strip())
                    })
                    current_text = ""

                current_header = text
            else:
                current_text += " " + text

        # 處理最後一筆發言
        if current_text.strip():
            turn_count += 1
            seg_id = f"{call_id}_turn_{turn_count:02d}"
            segments.append({
                "segment_id": seg_id,
                "call_id": call_id,
                "section_type": "qa",
                "question_id": f"Q_{turn_count:02d}",
                "transcript_text": f"{current_header} {current_text}".strip(),
                "word_count": len(current_text.strip())
            })

    return segments


async def save_segments_to_db(segments: list):
    """
    通用型寫入 MySQL 資料庫
    """
    if not segments:
        print("⚠️ 沒有可寫入的片段資料。")
        return

    call_id = segments[0]["call_id"]
    parts = call_id.split("_")
    company_code = parts[0] if len(parts) > 0 else "UNKNOWN"
    
    try:
        pool = await aiomysql.create_pool(
            host=os.getenv("DB_HOST"),
            port=int(os.getenv("DB_PORT", 3306)),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            db=os.getenv("DB_NAME"),
            autocommit=True
        )

        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                # 自動判斷公司代號與名稱
                sql_call = """
                    INSERT INTO earnings_calls 
                    (call_id, company_code, company_name, fiscal_year, call_date)
                    VALUES (%s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE updated_at=CURRENT_TIMESTAMP
                """
                await cur.execute(sql_call, (
                    call_id, company_code, f"公司_{company_code}", 2026, "2026-01-01"
                ))

                sql_segment = """
                    INSERT INTO speech_segments 
                    (segment_id, call_id, section_type, question_id, start_time_sec, end_time_sec, transcript_text, word_count)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE 
                    section_type=VALUES(section_type), 
                    question_id=VALUES(question_id), 
                    transcript_text=VALUES(transcript_text)
                """

                for seg in segments:
                    await cur.execute(sql_segment, (
                        seg["segment_id"],
                        seg["call_id"],
                        seg["section_type"],
                        seg["question_id"],
                        0.0,
                        0.0,
                        seg["transcript_text"],
                        seg["word_count"]
                    ))

                print(f"✅ 成功將 [{call_id}] 的 {len(segments)} 個 Speaker Turn 寫入/更新至 `speech_segments` 表！")

        pool.close()
        await pool.wait_closed()

    except Exception as e:
        print(f"❌ 資料庫寫入失敗：{e}")