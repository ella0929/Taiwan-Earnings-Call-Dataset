import os
import asyncio
import aiomysql
from dotenv import load_dotenv

# 載入 .env 檔案中的隱藏變數
load_dotenv()

async def test_connection():
    try:
        # 建立非同步連線池 (Connection Pool)
        pool = await aiomysql.create_pool(
            host=os.getenv("DB_HOST"),
            port=int(os.getenv("DB_PORT", 3306)),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            db=os.getenv("DB_NAME"),
            autocommit=True
        )

        # 從連線池中取得一條連線
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                # 測試查詢剛剛建立的表
                await cur.execute("SHOW TABLES;")
                tables = await cur.fetchall()
                print("成功連線！目前資料庫有的表：")
                for table in tables:
                    print(f"- {table[0]}")

        # 關閉連線池
        pool.close()
        await pool.wait_closed()

    except Exception as e:
        print(f"連線失敗：{e}")

# 執行非同步測試
if __name__ == "__main__":
    asyncio.run(test_connection())
    