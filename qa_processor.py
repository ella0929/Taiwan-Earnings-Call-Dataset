import os
import re
import asyncio
import aiomysql
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

DB_CONFIG = {
    'host': os.getenv('DB_HOST'),          # 從 .env 讀取，沒有預設值（安全）
    'user': os.getenv('DB_USER'),          # 從 .env 讀取
    'password': os.getenv('DB_PASSWORD'),  # 從 .env 讀取
    'db': os.getenv('DB_NAME'),            # 從 .env 讀取
    'port': int(os.getenv('DB_PORT', 3306)), # 3306 為公開標準 Port，可安心留著
    'autocommit': True
}

async def classify_segment_with_gemini(text: str) -> str:
    prompt = f"""
你是一位嚴格的法說會文本分類器。請分析以下段落，判斷是屬於【簡報階段 PRESENTATION】還是【問答階段開端 QA_START】。

【核心判定指令】
請特別注意：分析師提問時常以客套話（如 "Thank you", "Very clear"）開頭。請忽視開頭客套話，直接檢視段落中後段是否包含提問意圖：
1. 包含問句或請求：如 "could you", "can you", "give us more ", "share a little bit", "my question is", "?"。
2. 主持人宣告開放提問：如 "open for questions", "turn over for Q&A"。
3. 有問號。

只要滿足上述任一條件，一律強制輸出：QA_START。

【範例參考】
文字："OK, thank you very much, Asi. This is very clear. I think just starting from the smartphone or core business, could you share a little bit about if there's any opportunities? Could you give us more color on that?"
判定：QA_START

【待分析文字】
"{text}"

【輸出格式要求】
請嚴格只輸出標籤名稱：PRESENTATION 或 QA_START。
"""
    try:
        response = await client.aio.models.generate_content(
            model='gemini-3.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=50,
                tools=[]
            )
        )
        if response and response.text:
            return response.text.strip().upper()
        return "PRESENTATION"
    except Exception as e:
        print(f"Gemini API 呼叫失敗: {e}")
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

            qa_keywords = ["question", "q&a", "qa", "answer", "turn over", "open for", "問答", "提問", "請問", "?","could you share", "give us more ", "my first question",
                            "my question is", "can you talk about", "wondering if you",
                            "open the floor", "turn the call over"]

            for group in merged_segments:
                full_text = " ".join(group['texts'])

                #  第一階段：利用 Gemini 尋找第一個問答開端 (Q1)
                if not found_qa_start:
                  
                    if group['start_time_sec'] > 300 and any(kw in full_text.lower() for kw in qa_keywords):
                        print(f"在 {group['start_time_sec']} 秒處發現關鍵字，呼叫 Gemini 驗證 Q1 開端...")
                        llm_label = await classify_segment_with_gemini(full_text[:300])
                        clean_label = llm_label.strip().upper()
                        print(f"Gemini 判定結果: '{clean_label}'")        
                       
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