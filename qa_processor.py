import os
import re
import asyncio
import aiomysql
from dotenv import load_dotenv

load_dotenv()

def parse_clean_text(transcript_text: str, call_id: str):
    """
    通用型逐字稿解析器：相容台積電、聯發科等不同公司的中英文法說會逐字稿格式
    直接傳入隊友從 MySQL (transcript_segments) 清洗出的 clean_text
    """
    if not transcript_text:
        return []

    # 1. 中英文 Q&A 區段開頭關鍵字
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

    # 3. 解析 Speaker Turn（過濾純標題與無效內容）
    if qa_text:
        speaker_pattern = r'(\n(?:\*\*[^*]+\*\*|[A-Z][a-zA-Z\.\s]+[：:]|[\u4e00-\u9fa5]{2,4}(?:董事長|總裁|副總|分析師|提問)?[\s：:]|Q\d*|A\d*|Operator|提問|回答)[：:]?)'
        
        raw_blocks = re.split(speaker_pattern, qa_text)

        turn_count = 0
        current_header = ""
        current_text = ""

        # 用來比對是否為無意義標題的清單
        junk_keywords = ["q&a", "question-and-answer session", "問答", "提問", "問與答"]

        for block in raw_blocks:
            text = block.strip()
            if not text:
                continue

            # 驗證此區塊是否為講者標頭
            is_header = re.match(r'^(\*\*[^*]+\*\*|[A-Z][a-zA-Z\.\s]+[：:]?|[\u4e00-\u9fa5]{2,4}(?:董事長|總裁|副總|分析師|提問)?[\s：:]?|Q\d*|A\d*|Operator|提問|回答)[：:]?$', text, re.IGNORECASE)

            if is_header:
                clean_body = current_text.strip()
                # 判斷內容是否為有效發言（非純標題，且有實質內文）
                if clean_body and clean_body.lower() not in junk_keywords and len(clean_body) > 2:
                    turn_count += 1
                    seg_id = f"{call_id}_turn_{turn_count:02d}"
                    segments.append({
                        "segment_id": seg_id,
                        "call_id": call_id,
                        "section_type": "qa",
                        "question_id": f"Q_{turn_count:02d}",
                        "transcript_text": f"{current_header} {clean_body}".strip(),
                        "word_count": len(clean_body)
                    })
                current_text = ""
                current_header = text
            else:
                current_text += " " + text

        # 處理最後一筆，同樣過濾無效發言
        clean_body = current_text.strip()
        if clean_body and clean_body.lower() not in junk_keywords and len(clean_body) > 2:
            turn_count += 1
            seg_id = f"{call_id}_turn_{turn_count:02d}"
            segments.append({
                "segment_id": seg_id,
                "call_id": call_id,
                "section_type": "qa",
                "question_id": f"Q_{turn_count:02d}",
                "transcript_text": f"{current_header} {clean_body}".strip(),
                "word_count": len(clean_body)
            })

    return segments


async def save_segments_to_db(pool, call_id: str, segments: list):
    """
    寫入 MySQL 資料庫 speech_segments（寫入前先清理舊的髒資料）
    """
    if not segments:
        print(f"⚠️ [{call_id}] 沒有可寫入的片段資料。")
        return

    parts = call_id.split("_")
    company_code = parts[0] if len(parts) > 0 else "UNKNOWN"
    
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                # 1. 父表卡位 (earnings_calls)
                sql_call = """
                    INSERT INTO earnings_calls 
                    (call_id, company_code, company_name, fiscal_year, call_date)
                    VALUES (%s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE updated_at=CURRENT_TIMESTAMP
                """
                await cur.execute(sql_call, (
                    call_id, company_code, f"公司_{company_code}", 2026, "2026-01-01"
                ))

                # 2. 清理該 call_id 舊有的 speech_segments 資料
                await cur.execute("DELETE FROM speech_segments WHERE call_id = %s", (call_id,))

                # 3. 寫入全新的乾淨 segments
                sql_segment = """
                    INSERT INTO speech_segments 
                    (segment_id, call_id, section_type, question_id, start_time_sec, end_time_sec, transcript_text, word_count)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
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

                print(f"✅ 成功清理舊資料並寫入 [{call_id}] 的 {len(segments)} 個乾淨 Speaker Turn！")

    except Exception as e:
        print(f"❌ 資料庫寫入失敗 [{call_id}]：{e}")