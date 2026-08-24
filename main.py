import os
import asyncio
import aiomysql
from qa_processor import parse_clean_text, save_segments_to_db

# 資料庫連線設定（請填入你們團隊的資料庫資訊）
import asyncio
import aiomysql
from qa_processor import parse_clean_text, save_segments_to_db

# 雲端 Clever Cloud MySQL 資料庫設定
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT", 3306)),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "db": os.getenv("DB_NAME"),
}

async def main():
    pool = await aiomysql.create_pool(**DB_CONFIG)
    
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            # 1. 從隊友的表取得可用的 call_id (如 2454_20260311)
            await cur.execute("SELECT DISTINCT call_id FROM transcript_segments")
            calls = await cur.fetchall()
            
            if not calls:
                print("⚠️ transcript_segments 表中目前沒有任何資料可供處理！")
                return

            print(f"📁 找到 {len(calls)} 場法說會資料，準備從資料庫讀取並解析...\n")

            # 2. 逐一處理每一場法說會
            for call in calls:
                call_id = call['call_id']
                print(f"--------------------------------------------------")
                print(f"📂 [正在處理法說會]：{call_id}")

                try:
                    # 3. 撈取隊友 Whisper 洗好的 clean_text
                    await cur.execute(
                        """
                        SELECT clean_text, start_time_sec, end_time_sec 
                        FROM transcript_segments 
                        WHERE call_id = %s 
                        ORDER BY segment_id ASC
                        """,
                        (call_id,)
                    )
                    rows = await cur.fetchall()
                    full_clean_text = "\n".join([r['clean_text'] for r in rows if r['clean_text']])

                    # 4. 呼叫你的 QA 解析器
                    parsed_segments = parse_clean_text(full_clean_text, call_id)
                    print(f"  └─ 🔍 共解析出 {len(parsed_segments)} 個 Speaker/Q&A 區段")

                    # 5. 寫入 speech_segments 資料表
                    print("  └─ 💾 正在寫入 MySQL (speech_segments)...")
                    await save_segments_to_db(pool, call_id, parsed_segments)
                    print(f"  └─ ✨ {call_id} 處理完成！")

                except Exception as e:
                    print(f"  └─ ❌ 處理 {call_id} 時發生錯誤：{e}")

    pool.close()
    await pool.wait_closed()
    print(f"\n🎉 所有法說會資料已全部處理完畢！")

if __name__ == "__main__":
    asyncio.run(main())