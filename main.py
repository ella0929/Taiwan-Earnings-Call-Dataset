import os
import asyncio
from qa_processor import parse_txt_file, save_segments_to_db

async def main():
    # 指向你的 txt 逐字稿檔案路徑
    file_path = "2454_2026Q2.txt"

    if not os.path.exists(file_path):
        print(f"❌ 找不到檔案：{file_path}，請確認檔案是否已放在專案根目錄下。")
        return

    print(f"📂 開始讀取並解析檔案：{file_path} ...")
    
    # 1. 讀取並切割 QA 內容
    segments = parse_txt_file(file_path)
    print(f"🔍 共解析出 {len(segments)} 個區段（包含簡報與問答對）")

    # 2. 寫入 MySQL 資料庫
    print("💾 正在寫入 MySQL 資料庫...")
    await save_segments_to_db(segments)

if __name__ == "__main__":
    asyncio.run(main())