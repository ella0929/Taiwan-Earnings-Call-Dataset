import os
import re
import asyncio
import aiomysql
from dotenv import load_dotenv

# 載入 .env 檔案中的隱藏變數
load_dotenv()

def parse_txt_file(file_path: str):
    """
    1. 讀取 .txt 文字檔
    2. 自動抓取 Q&A 開始點
    3. 將文字切割成 speech_segments 所需的結構
    """
    # 從檔名取得 call_id (例: "2454_2026Q2.txt" -> "2454_2026Q2")
    file_name = os.path.basename(file_path)
    call_id = os.path.splitext(file_name)[0]

    # 讀取文字檔內容
    with open(file_path, "r", encoding="utf-8") as f:
        transcript_text = f.read()

    # 定義 Q&A 的開頭關鍵字
    qa_keywords = [r"Q&A", r"q&a", r"問答", r"提問", r"問與答", r"開放提問", r"現場提問"]
    qa_start_pos = -1

    for kw in qa_keywords:
        match = re.search(kw, transcript_text)
        if match:
            qa_start_pos = match.start()
            break

    segments = []

    # 切割簡報 (presentation) 與 Q&A 區段
    if qa_start_pos != -1:
        pres_text = transcript_text[:qa_start_pos].strip()
        qa_text = transcript_text[qa_start_pos:].strip()

        # 寫入簡報區段
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

    # 解析 Q&A 區段中的每一組問答
    if qa_text:
        # 尋找常見的問答分界點 (例: Q1:, A:, 提問:, 回答:, 法人:)
        blocks = re.split(r'(\n(?:Q\d*|A\d*|提問|回答|法人|分析師|公司回應)[：:]?)', qa_text)

        q_count = 0
        current_q = ""
        current_a = ""
        is_question = True

        for block in blocks:
            text = block.strip()
            if not text:
                continue

            # 判斷是否為「問題」標頭
            if re.match(r'^(Q\d*|提問|法人|分析師)[：:]?$', text, re.IGNORECASE):
                if current_q or current_a:
                    q_count += 1
                    qid = f"Q{q_count}"
                    if current_q:
                        segments.append({
                            "segment_id": f"{call_id}_{qid}_Q",
                            "call_id": call_id,
                            "section_type": "qa",
                            "question_id": qid,
                            "transcript_text": current_q.strip(),
                            "word_count": len(current_q.strip())
                        })
                    if current_a:
                        segments.append({
                            "segment_id": f"{call_id}_{qid}_A",
                            "call_id": call_id,
                            "section_type": "qa",
                            "question_id": qid,
                            "transcript_text": current_a.strip(),
                            "word_count": len(current_a.strip())
                        })
                    current_q, current_a = "", ""
                is_question = True

            # 判斷是否為「回答」標頭
            elif re.match(r'^(A\d*|回答|公司回應)[：:]?$', text, re.IGNORECASE):
                is_question = False
            else:
                if is_question:
                    current_q += " " + text
                else:
                    current_a += " " + text

        # 處理最後一組 Q&A
        if current_q or current_a:
            q_count += 1
            qid = f"Q{q_count}"
            if current_q:
                segments.append({
                    "segment_id": f"{call_id}_{qid}_Q",
                    "call_id": call_id,
                    "section_type": "qa",
                    "question_id": qid,
                    "transcript_text": current_q.strip(),
                    "word_count": len(current_q.strip())
                })
            if current_a:
                segments.append({
                    "segment_id": f"{call_id}_{qid}_A",
                    "call_id": call_id,
                    "section_type": "qa",
                    "question_id": qid,
                    "transcript_text": current_a.strip(),
                    "word_count": len(current_a.strip())
                })

    return segments


async def save_segments_to_db(segments: list):
    """
    連接 MySQL 資料庫，並將處理好的 Q&A 切割結果寫入 `speech_segments` 資料表
    """
    if not segments:
        print("⚠️ 沒有可寫入的片段資料。")
        return

    # 取得當前處理的 call_id (如 "2454_2026Q2")
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
                # 1. 補全父表 earnings_calls (如果不存在就自動建立基本紀錄，解決外鍵報錯)
                sql_call = """
                    INSERT INTO earnings_calls 
                    (call_id, company_code, company_name, fiscal_year, call_date)
                    VALUES (%s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE updated_at=CURRENT_TIMESTAMP
                """
                await cur.execute(sql_call, (
                    call_id,
                    company_code,
                    f"公司_{company_code}",  # 預設名稱，後續可由負責人改掉
                    2026,                  # 預設年份
                    "2026-01-01"           # 預設日期
                ))

                # 2. 寫入 speech_segments 子表
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
                        0.0,  # 預留給處理聲音/時間軸的同學填入
                        0.0,  # 預留給處理聲音/時間軸的同學填入
                        seg["transcript_text"],
                        seg["word_count"]
                    ))

                print(f"✅ 成功將 {len(segments)} 筆資料寫入/更新至 `speech_segments` 表！")

        pool.close()
        await pool.wait_closed()

    except Exception as e:
        print(f"❌ 資料庫寫入失敗：{e}")