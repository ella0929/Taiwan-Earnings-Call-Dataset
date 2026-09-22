import os
import re
import asyncio
import aiomysql
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    'host': os.getenv('DB_HOST'),          # 從 .env 讀取，沒有預設值（安全）
    'user': os.getenv('DB_USER'),          # 從 .env 讀取
    'password': os.getenv('DB_PASSWORD'),  # 從 .env 讀取
    'db': os.getenv('DB_NAME'),            # 從 .env 讀取
    'port': int(os.getenv('DB_PORT', 3306)), # 3306 為公開標準 Port，可安心留著
    'autocommit': True
}

def classify_segment_with_keywords(text: str) -> str:
    question_keywords = [
        "想請教",
        "請教",
        "想詢問",
        "想了解",
        "請幫我們說明",
        "能否分享",
        "我的問題是",
        "策略是為何",
        "狀況為何",
        "展望為何",
        "could you",
        "can you",
        "give us more",
        "share a little bit",
        "my question is",
        "can you talk about",
        "wondering if you",
        "?",
    ]

    presentation_keywords = [
        "現在進行 q&a",
        "開放提問",
        "有問題的法人可以提出問題",
        "請線上分析師發問",
        "open the floor",
        "turn the call over",
    ]

    normalized_text = text.lower()

    if any(keyword in normalized_text for keyword in presentation_keywords):
        return "PRESENTATION"

    if any(keyword in normalized_text for keyword in question_keywords):
        return "QA_START"

    return "PRESENTATION"

async def process_qa_pipeline(call_id: str):
    conn = await aiomysql.connect(**DB_CONFIG)
   
    try:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            # 1. 撈取 raw_text 資料
            sql_fetch = """
                SELECT segment_id, call_id, speaker_id, raw_text, start_time_sec, end_time_sec
                FROM transcript_segments
                WHERE call_id = %s
                ORDER BY start_time_sec ASC
            """
            await cur.execute(sql_fetch, (call_id,))
            raw_segments = await cur.fetchall()

            if not raw_segments:
                print(f"在 transcript_segments 找不到 call_id = {call_id} 的資料。")
                return

            print(f"已讀取 {len(raw_segments)} 筆逐字稿片段")

            # 2. 合併同一講者的 raw_text
            merged_segments = []
            current_group = None

            for seg in raw_segments:
                raw_text = seg.get('raw_text') or ''
                cleaned_raw_text = re.sub(r'\[\d+\.\d+\s*-->\s*\d+\.\d+\]', '', raw_text).strip()
               
                if not cleaned_raw_text:
                    continue

                spk_id = seg.get('speaker_id')
                is_different_speaker = (current_group is None or current_group['speaker_id'] != spk_id)

                if is_different_speaker:
                    if current_group is not None:
                        merged_segments.append(current_group)

                    current_group = {
                        'segment_id': seg['segment_id'],
                        'call_id': seg['call_id'],
                        'speaker_id': spk_id,
                        'start_time_sec': seg['start_time_sec'],
                        'end_time_sec': seg['end_time_sec'],
                        'texts': [cleaned_raw_text]
                    }
                else:
                    current_group['texts'].append(cleaned_raw_text)
                    current_group['end_time_sec'] = seg['end_time_sec']

            if current_group is not None:
                merged_segments.append(current_group)

            print(f"合併後共 {len(merged_segments)} 個段落，搜尋 Q1 轉折點...")

            processed_segments = []
            current_section = "presentation"
            found_qa_start = False
            qa_speaker_change_count = 0 

            qa_keywords = ["question", "q&a", "qa", "answer", "turn over", "open for", "提問", "請問", "?","could you share", "give us more ", "my first question",
                            "my question is", "can you talk about", "wondering if you",
                            "open the floor", "turn the call over","想請教", "請教", "想詢問", "想了解", "策略是為何", "展望為何"]

            for group in merged_segments:
                full_text = " ".join(group['texts'])

                #  第一階段：利用關鍵字尋找第一個問答開端 (Q1)
                if not found_qa_start:
                  
                    if group['start_time_sec'] > 300 and any(kw in full_text.lower() for kw in qa_keywords):
                        print(f"在 {group['start_time_sec']} 秒處發現關鍵字，使用關鍵字判斷 Q1 開端...")
                        keyword_label = classify_segment_with_keywords(full_text[:400])
                        clean_label = keyword_label.strip().upper()
                        print(f"關鍵字判定結果: '{clean_label}'")        
                       
                        if "QA_" in clean_label:
                            current_section = "qa"
                            found_qa_start = True
                            print(f"在 {group['start_time_sec']} 秒處找到 Q1！")
                           

                # 第二階段：純靠 Speaker ID 切換自動分發 Q1, Q2, Q3...
                question_id = None
                if current_section == "qa":
                    qa_speaker_change_count += 1
                    current_q = ((qa_speaker_change_count - 1) // 2) + 1
                    question_id = f"Q_{current_q:02d}"
                    print(f"[ID 切換標記] 講者 {group['speaker_id']} (第 {qa_speaker_change_count} 次講者交替) 分配至編號: {question_id}")

                processed_segments.append({
                    'segment_id': group['segment_id'],
                    'call_id': group['call_id'],
                    'speaker_id': group['speaker_id'],
                    'section_type': current_section,
                    'question_id': question_id,
                    'start_time_sec': group['start_time_sec'],
                    'end_time_sec': group['end_time_sec'],
                    'transcript_text': full_text
                })

            await cur.execute("DELETE FROM speech_segments WHERE call_id = %s", (call_id,))

            sql_insert = """
                INSERT INTO speech_segments (
                    segment_id, call_id, speaker_id, section_type,
                    question_id, start_time_sec, end_time_sec, transcript_text
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """
            insert_data = [
                (
                    s['segment_id'], s['call_id'], s['speaker_id'], s['section_type'],
                    s['question_id'], s['start_time_sec'], s['end_time_sec'], s['transcript_text']
                )
                for s in processed_segments
            ]
           
            await cur.executemany(sql_insert, insert_data)
            print(f"寫入 {len(insert_data)} 筆資料至 speech_segments！")

    finally:
        conn.close()
        await conn.ensure_closed()