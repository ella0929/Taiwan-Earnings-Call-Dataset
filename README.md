# Taiwan Earnings Call Dataset

從法說會影音產生逐字稿、Metadata、QC、講者配對及問答段落。主要入口為 `run_all.py`。

## 安裝與設定

使用 Python 3.11 或 3.12 的獨立環境，並確保 FFmpeg 已在 PATH：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
ffmpeg -version
```

套件列出相容的大版本範圍，尚非已驗證於每部 Windows/GPU 的鎖定環境。
若已有可正常執行的環境，先使用原環境，無須重新建立。
GPU 的 CUDA/cuDNN 需配合 [Faster-Whisper 官方安裝說明](https://github.com/SYSTRAN/faster-whisper)。
沒有 GPU 時可將 `WHISPER_DEVICE=cpu`、`WHISPER_COMPUTE_TYPE=int8`。
Pyannote 模型需要 Hugging Face read token，並接受
[community-1 模型條款](https://huggingface.co/pyannote/speaker-diarization-community-1)。

參照 `.env.example` 設定專案根目錄的 `.env`，不要覆蓋既有的有效設定。
所有入口均從專案根目錄讀取 `.env`；已設定的系統環境變數優先。
首次使用模型會下載模型檔案。

MySQL 需有本專案既有的 `earnings_calls`、`transcript_segments`、`transcript_words`、
`speakers`、`speech_segments` 等資料表。這個專案沒有完整建表 SQL，請沿用既有資料庫。
`speech_segments` 必須使用 InnoDB；程式會先檢查，確保失敗時能回復。
若舊欄位不允許 Metadata 待補，執行一次：

```powershell
python scripts/allow_pending_metadata.py
```

此指令需 ALTER 權限，只放寬公司名稱、財報年度、季度的 NULL 限制。
主流程不會自動執行資料庫結構遷移。

## 執行

檔名規則：`公司代號_YYYYMMDD`；支援 MP4、MOV、MKV、AVI、WAV。
輸入檔案放在 `data/raw`，不要把輸出 WAV 當作來源。

```powershell
python run_all.py data/raw/4104_20260915.mp4
```

省略路徑時只有一部候選影音才會自動選取。相對影音路徑以專案根目錄為基準；
`--announcement` 路徑以目前終端機目錄為基準。

七個步驟依序為：轉錄 → 清理 → Metadata → QC → MySQL → 講者 → QA。
QC 只執行一次。`run_pipeline.py` 是相同完整流程的相容別名，不再是另一套流程。
`main.py <call_id>` 僅執行已完成資料庫匯入與講者配對的 QA。

```powershell
python run_all.py data/raw/4104_20260915.mp4 --force
python run_all.py data/raw/4104_20260915.mp4 --offline
python run_all.py data/raw/4104_20260915.mp4 --source-url "https://example.com/video"
python run_all.py data/raw/4104_20260915.mp4 --announcement data/raw/4104_20260915.announcement.txt
```

`--force` 重做影音轉換、轉錄和講者辨識。`--offline` 只限制 Metadata 公司／公告查詢；
不代表 MySQL 或尚未下載的模型能離線使用。
`--verbose` 顯示講者配對明細；預設只顯示主要進度與結果。

## 重跑與結果狀態

- WAV、Raw、Diarization 快取檢查來源 SHA-256、參數及輸出雜湊；同名影音內容改變時不沿用舊結果。
- 舊版本沒有快取指紋，升級後首次可能重新轉錄／辨識一次。之後符合快取才跳過。
- 清理、QC、匯入與 QA 會重新處理。QA 刪除與新增在同一筆交易中，失敗會 rollback。
- `output/<call_id>/pipeline_status.json` 保存各步驟狀態。
- exit 0：處理完成；若 Metadata 或 QC 待核對，狀態為 `completed_with_review`。
- exit 1：執行錯誤；exit 2：需要核對，例如講者配對不完整或 QA 無法可靠分類。
- 講者配對未完成，不執行 QA；QA 待核對時保存候選報告，保留舊 `speech_segments`。

輸出在 `output/<call_id>/`：WAV、Raw/Clean JSON/TXT、Metadata、QC 三份報告、
Diarization、Speaker matching、QA review、快取指紋與流程狀態。
資料庫的既有非空財報期間不會被空 Metadata 清除；只有人工確認期間會更新。
QC 和 QA 都是自動規則結果，不等於人工驗證準確率。

## QA 核對

QA 使用逐字稿片段判斷過場、提問與回答，沒有「前 300 秒不判斷」或「每兩位講者一題」限制。
同一题可以由多人回答；辨識為主持人過場時不分配題號。
語意不明、沒有講者或無回答等情況不假裝成功。

檢查 `<call_id>_qa_review.json`，必要時建立同資料夾的 `<call_id>_qa_overrides.json`：

```json
{
  "4104_20260915_seg_0001": "question",
  "4104_20260915_seg_0002": "answer"
}
```

值可為 `question`、`answer`、`moderator`、`presentation`；鍵必須使用實際 segment_id。
之後執行 `python main.py 4104_20260915` 重做 QA。
詳細 Metadata 規則與人工修正方式見 [METADATA.md](METADATA.md)。

## 驗證

```powershell
python -m unittest test_metadata test_pipeline -v
```

測試使用假資料、假模型及假資料庫，涵蓋快取失效、停止狀態、交易 rollback、
QA 多人回答、公告日期配對、Metadata 覆寫與缺值。不是 GPU/MySQL 的完整整合測試。
