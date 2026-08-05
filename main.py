import os
import glob
import asyncio
from qa_processor import parse_txt_file, save_segments_to_db

async def main():
    # 1. 設定放置 .txt 檔案的資料夾路徑 ('.' 代表目前專案根目錄)
    folder_path = "."
    
    # 2. 自動搜尋資料夾內所有符合 *.txt 的檔案
    txt_files = glob.glob(os.path.join(folder_path, "*.txt"))
    
    # 排除不是法說會逐字稿的檔案（例如 requirements.txt）
    ignore_files = ["requirements.txt"]
    target_files = [f for f in txt_files if os.path.basename(f) not in ignore_files]

    if not target_files:
        print("❌ 找不到任何可以處理的 .txt 法說會檔案！")
        return

    print(f"📁 找到 {len(target_files)} 個逐字稿檔案，準備開始批次處理...\n")

    # 3. 使用迴圈自動處理每一個檔案
    for file_path in target_files:
        file_name = os.path.basename(file_path)
        print(f"--------------------------------------------------")
        print(f"📂 [正在處理]：{file_name}")

        try:
            # 讀取並切割 QA 內容
            segments = parse_txt_file(file_path)
            print(f"  └─ 🔍 共解析出 {len(segments)} 個區段")

            # 寫入 MySQL 資料庫
            print("  └─ 💾 正在寫入 MySQL 資料庫...")
            await save_segments_to_db(segments)
            print(f"  └─ ✨ {file_name} 處理完成！")

        except Exception as e:
            print(f"  └─ ❌ 處理檔案 {file_name} 時發生錯誤：{e}")

    print(f"\n🎉 所有檔案已全部處理完畢！")

if __name__ == "__main__":
    asyncio.run(main())