"""Extract candidates, never equate a mentioned quarter with confirmed call scope."""
import re
import unicodedata

PATTERNS = (
    r"(?P<y>20\d{2}|1\d{2})\s*年(?:度)?(?:的)?\s*(?:第\s*)?(?P<q>[一二三四1-4])\s*季",
    r"(?P<y>20\d{2}|1\d{2})\s*(?:年(?:度)?(?:的)?)?\s*Q(?P<q>[1-4])",
    r"Q(?P<q>[1-4])\s*(?P<y>20\d{2})",
)


def candidates(text, source):
    text = unicodedata.normalize("NFKC", text)
    result = []
    quarters = dict(zip("一二三四1234", [1, 2, 3, 4, 1, 2, 3, 4]))
    for pattern in PATTERNS:
        for match in re.finditer(pattern, text, re.I):
            year = int(match["y"])
            year = year + 1911 if year < 1911 else year
            context = text[max(0, match.start()-40):match.end()+60]
            result.append({"fiscal_year": year, "fiscal_quarter": f"Q{quarters[match['q']]}",
                           "source": source, "evidence": context,
                           "comparison_context": bool(re.search(r"去年|同期|比較|比较|compared|prior year|last year", context, re.I))})
    return result


def validate_overrides(values):
    allowed = {"company_name", "industry", "industry_code", "source_url", "audio_url", "transcript_url", "fiscal_year", "fiscal_quarter"}
    if not isinstance(values, dict) or set(values) - allowed:
        raise ValueError("Metadata manual_overrides 含不支援的欄位")
    year, quarter = values.get("fiscal_year"), values.get("fiscal_quarter")
    if year is not None and (type(year) is not int or not 1900 <= year <= 2200):
        raise ValueError("fiscal_year 必須為西元整數年份")
    if quarter is not None and quarter not in {"Q1", "Q2", "Q3", "Q4"}:
        raise ValueError("fiscal_quarter 必須為 Q1、Q2、Q3、Q4")
