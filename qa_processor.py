import re
import aiomysql
import os

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'bz0brszlpyzt17u2p7pg-mysql.services.clever-cloud.com'),
    'user': os.getenv('DB_USER', 'ubnvtjwgdicnkxoi'),
    'password': os.getenv('DB_PASSWORD', 'aQkWesRWac3Zs5jVLLzI'),
    'db': os.getenv('DB_NAME', 'bz0brszlpyzt17u2p7pg'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'autocommit': True
}

async def process_qa_pipeline(call_id: str):
    conn = await aiomysql.connect(**DB_CONFIG)
    
    try:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            # 1. 撈取資料（只查詢存在的欄位）
            sql_fetch = """
                SELECT segment_id, clean_text, start_time_sec, end_time_sec 
                FROM transcript_segments 
                WHERE call_id = %s 
                ORDER BY start_time_sec ASC
            """
            await cur.execute(sql_fetch, (call_id,))
            raw_segments = await cur.fetchall()

            if not raw_segments:
                print(f"⚠️ 找不到 call_id = {call_id} 的逐字稿資料。")
                return

            print(f"📥 成功從 transcript_segments 讀取 {len(raw_segments)} 筆逐字稿片段...")

            # 廣義關鍵字比對
            qa_trigger_pattern = re.compile(
                r'(see if you guys have other questions|questions?|q&a|open for|問答|提問)', 
                re.IGNORECASE
            )
            new_q_pattern = re.compile(
                r'^(could you|moving on|besides|is there|can you|my next|my first|first question|second question|我想請問)', 
                re.IGNORECASE
            )

            processed_segments = []
            current_section = "presentation"
            q_count = 0
            turn_index = 1

            for seg in raw_segments:
                text = seg.get('clean_text') or ''
                clean_text = re.sub(r'\[\d+\.\d+\s*-->\s*\d+\.\d+\]', '', text).strip()
                
                if not clean_text:
                    continue

                # 判斷轉折點進入 QA 階段
                if current_section == "presentation":
                    if qa_trigger_pattern.search(clean_text):
                        current_section = "qa"

                # 計算問答編號
                question_id = None
                if current_section == "qa":
                    if new_q_pattern.search(clean_text):
                        q_count += 1
                    current_q = max(1, q_count)
                    question_id = f"Q_{current_q:02d}"

                segment_id = seg['segment_id']
                
                processed_segments.append({
                    'segment_id': segment_id,
                    'call_id': call_id,
                    'speaker_id': None,
                    'section_type': current_section,
                    'question_id': question_id,
                    'start_time_sec': seg['start_time_sec'],
                    'end_time_sec': seg['end_time_sec'],
                    'transcript_text': clean_text
                })
                turn_index += 1

            # 2. 寫入 speech_segments 資料表
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
            print(f"🎉 成功寫入全量 {len(insert_data)} 筆資料至 speech_segments！")

    finally:
        # 正確關閉連線，避免 Event loop is closed 警告
        conn.close()