import sys
import asyncio
from qa_processor import process_qa_pipeline

async def main():
    # 檢查是否傳入 call_id 參數
    if len(sys.argv) < 2:
        print("❌ 錯誤：請提供 call_id 參數！")
        print("正確執行方式：python main.py <call_id>")
        print("例如：python main.py 2454_20260311")
        return

    call_id = sys.argv[1]
    print(f"🚀 開始處理法說會資料，call_id: {call_id}")
    
    await process_qa_pipeline(call_id)

if __name__ == "__main__":
    asyncio.run(main())