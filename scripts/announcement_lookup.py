"""Match official announcement snapshots by company and the actual meeting date."""
import csv
import io
import json
import re
from datetime import datetime, timezone
from urllib.request import Request, urlopen
try:
    from .pipeline_common import read_json, save_json
except ImportError:
    from pipeline_common import read_json, save_json

SOURCES = (
    "https://openapi.twse.com.tw/v1/opendata/t187ap04_L",
    "https://mopsfin.twse.com.tw/opendata/t187ap04_O.csv",
)


def fetch_announcements(url):
    with urlopen(Request(url, headers={"User-Agent": "EarningsCallMetadata/2.0"}), timeout=15) as response:
        text = response.read().decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text))) if url.endswith(".csv") else json.loads(text)
    if not isinstance(rows, list) or (rows and not isinstance(rows[0], dict)):
        raise ValueError("公告回應格式錯誤")
    if rows and "公司代號" not in {key.strip() for key in rows[0]}:
        raise ValueError("公告缺少公司代號欄位")
    return rows


def meeting_date(text):
    # Do not confuse announcement date / date of occurrence with meeting date.
    match = re.search(r"(?:召開|召开|舉行|举行)(?:法人說明會|法人说明会|法說會|法说会)(?:之)?日期\s*[:：]\s*(\d{3,4})[/.-](\d{1,2})[/.-](\d{1,2})", text)
    if not match:
        return None
    year, month, day = map(int, match.groups())
    if year < 1911:
        year += 1911
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return None


def lookup(code, call_date, cache_path, fetch=fetch_announcements, offline=False):
    cache = read_json(cache_path)
    today = datetime.now(timezone.utc).date().isoformat()
    records = cache.get("records", [])
    warnings = []
    refreshed = dict(cache.get("refreshed", {}))
    for url in SOURCES:
        if offline or refreshed.get(url) == today:
            continue
        try:
            rows = fetch(url)
            for original in rows:
                row = {key.strip(): value for key, value in original.items()}
                subject = str(row.get("主旨", ""))
                body = str(row.get("說明", ""))
                text = subject + "\n" + body
                if not re.search(r"法[人說说]*[說说]明會|法說會|法说会", text):
                    continue
                date = meeting_date(text)
                if date and row.get("公司代號"):
                    record = {"company_code": str(row["公司代號"]).strip(), "call_date": date,
                              "text": text, "source": url, "retrieved_at": today}
                    if not any(all(item.get(key) == record[key] for key in ("company_code", "call_date", "text", "source")) for item in records):
                        records.append(record)
            refreshed[url] = today
        except (OSError, ValueError, TypeError) as error:
            warnings.append(f"公告查詢失敗：{url} ({error})")
    if not offline:
        save_json(cache_path, {"records": records, "refreshed": refreshed})
    matches = [item for item in records if item["company_code"] == code and item["call_date"] == call_date]
    if not matches:
        warnings.append("官方公告快照／快取未找到公司與開會日期完全相符的公告；不代表公告不存在")
    return matches, warnings
