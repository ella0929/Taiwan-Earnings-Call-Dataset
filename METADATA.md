# Metadata 來源與覆寫

完整流程自動產生 `output/<call_id>/<call_id>_metadata.json`，不需先建立檔案。
只做 Metadata：`python scripts/generate_metadata.py data/raw/4104_20260915.mp4`。

## 來源

| 欄位 | 取得方式 |
| --- | --- |
| 公司代號、日期 | 檔名，驗證真實日期 |
| 公司名称、產業 | TWSE 上市／TPEx 上櫃基本資料，保存公司資料來源與抓取時間 |
| 語言、秒數 | Raw JSON 與 WAV |
| source_url | `--source-url`／人工覆寫優先，其次 `.info.json` 的 `webpage_url`、`.source.json` 的 `source_url` |
| 財報候選期間 | 公告文字與逐字稿抽取，保留引用文字、來源、是否可能涉及同期比較 |

公司快取在 `data/cache/company_profiles.json`，有效 30 天，更新失敗可沿用舊快取。
`--offline` 僅用本機資料與快取。

未指定公告時，自動查詢官方重大訊息快照，嚴格比對公司代號和「召開法人說明會日期」。
不將發言日期／事實發生日當成法說會日期。公告累积保存在 `data/cache/announcements.json`，
每個來源每天最多成功更新一次；歷史紀錄保留供之後重跑。
API 通常只含近期公告，查無舊場次不代表沒有公告。查詢失敗或未找到會記錄 warning。
支援的自動來源是公開公告文字，不會自動讀公司網站所有頁面或下載 PDF。
可用 `--announcement <UTF-8文字檔>` 或影音旁的 `<call_id>.announcement.txt` 補充公告內容。

## 財報狀態

- `manual`：有人工提供的完整年度與季度，可寫入資料庫。
- `inferred`：找到唯一候選期間，仍可能是比較或展望；主欄位維持空值。
- `ambiguous`：找到多個期間，保存全部候選供核對。
- `pending`：沒有可辨識期間，或資料不完整。

不以開會日期推算財報期間，不把「曾提到某季度」等同於「本場正在報告該季度」。
候選在 `fiscal_period_candidates`，待補欄位在 `pending_fields`。
現有資料庫期間只有非空、人工確認的新期間會更新；空值與候選不會清掉原值。
舊資料庫若不允許空值，先執行一次 `python scripts/allow_pending_metadata.py`。

## 人工修正

建議建立 `output/<call_id>/<call_id>_metadata_overrides.json`，內容例如：

```json
{
  "fiscal_year": 2026,
  "fiscal_quarter": "Q2",
  "source_url": "https://example.com/video"
}
```

只填你已確認的值。此檔優先於自動查詢，重跑不會覆蓋。
也可在 Metadata 的 `manual_overrides` 物件指定。
自動欄位有 `last_generated_values` 快照；升級後產生快照的欄位若被直接修改，
下次會自動視為人工覆寫。舊版沒有快照時無法追溯誰改過值，請用覆寫檔明確指定。
覆寫支援公司名稱、產業／產業代碼、三種網址、年度與季度。
年度為西元整數，季度為 Q1–Q4；空值不代表刪除資料庫既有值。
要恢復自動管理，須同時從覆寫檔及 `manual_overrides` 移除欄位，並移除 Metadata 裡該舊值。

完整流程會自動帶入覆寫；只要補 Metadata 到既有 DB，可執行：

```powershell
python scripts/generate_metadata.py data/raw/4104_20260915.mp4
```

這一步僅更新檔案；要同步資料庫請重跑完整流程。

官方來源：[TWSE API](https://openapi.twse.com.tw/)、[TPEx API](https://www.tpex.org.tw/openapi/)。
