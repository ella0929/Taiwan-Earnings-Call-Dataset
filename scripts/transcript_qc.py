"""為 Faster-Whisper raw.json 產生自動品質檢查報告。

QC 的用途是找出需要人工複查的高風險片段，不等同於真實準確率。
真實準確率仍需使用人工校正逐字稿計算 CER/WER。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from statistics import mean


DEFAULT_THRESHOLDS = {
    "low_word_probability": 0.50,
    "low_segment_avg_logprob": -1.0,
    "high_no_speech_probability": 0.60,
    "high_compression_ratio": 2.40,
    "long_segment_seconds": 30.0,
}


def format_timestamp(seconds: float | int | None) -> str:
    total_ms = round(float(seconds or 0) * 1000)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{milliseconds:03d}"


def normalize_text(text: str) -> str:
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE).lower()


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_reference_terms(metadata_path: Path | None) -> list[str]:
    if not metadata_path or not metadata_path.exists():
        return []

    metadata = load_json(metadata_path)
    terms: list[str] = []

    company_name = metadata.get("company_name")
    if isinstance(company_name, str) and company_name.strip():
        terms.append(company_name.strip())

    for key in ("speaker_name", "spokesperson"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            terms.append(value.strip())

    for key in ("known_terms", "glossary"):
        value = metadata.get(key, [])
        if isinstance(value, list):
            terms.extend(
                item.strip()
                for item in value
                if isinstance(item, str) and item.strip()
            )

    return list(dict.fromkeys(terms))


def analyze_transcript(
    raw_data: dict,
    source_path: Path,
    metadata_path: Path | None = None,
    thresholds: dict | None = None,
) -> dict:
    thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    segments = raw_data.get("segments") or []
    duration = float(raw_data.get("duration") or 0)
    review_segments = []
    all_probabilities: list[float] = []
    low_probability_word_count = 0
    missing_word_timestamp_count = 0
    previous_end = 0.0
    previous_normalized = ""

    for index, segment in enumerate(segments):
        start = segment.get("start")
        end = segment.get("end")
        start_num = float(start or 0)
        end_num = float(end or 0)
        text = str(segment.get("text") or "").strip()
        reasons: list[str] = []
        words = segment.get("words") or []
        probabilities = []
        low_words = []

        if start is None or end is None:
            reasons.append("缺少 segment 時間戳")
        elif end_num <= start_num:
            reasons.append("segment 結束時間不大於開始時間")

        if index and start_num < previous_end - 0.05:
            reasons.append("時間戳與前一段重疊或倒退")
        if duration and end_num > duration + 1.0:
            reasons.append("segment 超出音訊長度")
        if end_num - start_num > thresholds["long_segment_seconds"]:
            reasons.append("segment 時間過長")

        for word in words:
            probability = word.get("probability")
            if isinstance(probability, (int, float)):
                probability = float(probability)
                probabilities.append(probability)
                all_probabilities.append(probability)
                if probability < thresholds["low_word_probability"]:
                    low_probability_word_count += 1
                    low_words.append(str(word.get("word") or "").strip())

            word_start = word.get("start")
            word_end = word.get("end")
            if word_start is None or word_end is None:
                missing_word_timestamp_count += 1
            elif float(word_end) < float(word_start):
                missing_word_timestamp_count += 1

        if low_words:
            preview = "、".join(filter(None, low_words[:8]))
            reasons.append(f"低信心詞 {len(low_words)} 個：{preview}")

        avg_logprob = segment.get("avg_logprob")
        if isinstance(avg_logprob, (int, float)) and avg_logprob < thresholds["low_segment_avg_logprob"]:
            reasons.append("segment 平均 log probability 偏低")

        no_speech_prob = segment.get("no_speech_prob")
        if isinstance(no_speech_prob, (int, float)) and no_speech_prob > thresholds["high_no_speech_probability"]:
            reasons.append("疑似無語音或背景噪音")

        compression_ratio = segment.get("compression_ratio")
        if isinstance(compression_ratio, (int, float)) and compression_ratio > thresholds["high_compression_ratio"]:
            reasons.append("文字重複／幻覺風險偏高")

        normalized = normalize_text(text)
        if normalized and len(normalized) >= 8 and normalized == previous_normalized:
            reasons.append("與前一段文字重複")

        if reasons:
            review_segments.append({
                "segment_id": segment.get("id", index),
                "start_sec": round(start_num, 3),
                "end_sec": round(end_num, 3),
                "timestamp": f"{format_timestamp(start_num)} --> {format_timestamp(end_num)}",
                "text": text,
                "reasons": reasons,
                "low_confidence_words": low_words,
                "average_word_probability": (
                    round(mean(probabilities), 4) if probabilities else None
                ),
                "minimum_word_probability": (
                    round(min(probabilities), 4) if probabilities else None
                ),
                "avg_logprob": avg_logprob,
                "no_speech_prob": no_speech_prob,
                "compression_ratio": compression_ratio,
            })

        previous_end = max(previous_end, end_num)
        previous_normalized = normalized

    full_text = str(raw_data.get("text") or "")
    normalized_full_text = normalize_text(full_text)
    reference_terms = load_reference_terms(metadata_path)
    missing_reference_terms = [
        term for term in reference_terms
        if normalize_text(term) not in normalized_full_text
    ]

    total_words = len(all_probabilities)
    issue_counter = Counter(
        reason.split("：", 1)[0]
        for item in review_segments
        for reason in item["reasons"]
    )

    return {
        "report_type": "automatic_transcript_qc",
        "important_note": (
            "此報告只會找出疑似錯誤與高風險片段，不代表真實轉錄準確率；"
            "準確率需以人工校正樣本計算 CER/WER。"
        ),
        "source_raw_json": str(source_path),
        "metadata_json": str(metadata_path) if metadata_path else None,
        "thresholds": thresholds,
        "summary": {
            "language": raw_data.get("language"),
            "duration_seconds": duration,
            "segment_count": len(segments),
            "word_count": total_words,
            "average_word_probability": (
                round(mean(all_probabilities), 4) if all_probabilities else None
            ),
            "low_probability_word_count": low_probability_word_count,
            "low_probability_word_rate": (
                round(low_probability_word_count / total_words, 4)
                if total_words else None
            ),
            "missing_or_invalid_word_timestamp_count": missing_word_timestamp_count,
            "review_segment_count": len(review_segments),
            "review_segment_rate": (
                round(len(review_segments) / len(segments), 4)
                if segments else None
            ),
            "missing_reference_terms": missing_reference_terms,
            "issue_counts": dict(issue_counter),
        },
        "review_segments": review_segments,
    }


def write_report_files(report: dict, output_prefix: Path) -> dict[str, Path]:
    json_path = output_prefix.with_name(output_prefix.name + "_qc_report.json")
    csv_path = output_prefix.with_name(output_prefix.name + "_qc_review.csv")
    summary_path = output_prefix.with_name(output_prefix.name + "_qc_summary.txt")

    with json_path.open("w", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)

    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=[
            "segment_id", "start_sec", "end_sec", "timestamp", "reasons",
            "low_confidence_words", "average_word_probability",
            "minimum_word_probability", "text",
        ])
        writer.writeheader()
        for item in report["review_segments"]:
            writer.writerow({
                "segment_id": item["segment_id"],
                "start_sec": item["start_sec"],
                "end_sec": item["end_sec"],
                "timestamp": item["timestamp"],
                "reasons": "；".join(item["reasons"]),
                "low_confidence_words": "、".join(item["low_confidence_words"]),
                "average_word_probability": item["average_word_probability"],
                "minimum_word_probability": item["minimum_word_probability"],
                "text": item["text"],
            })

    summary = report["summary"]
    avg_probability = summary["average_word_probability"]
    low_rate = summary["low_probability_word_rate"]
    review_rate = summary["review_segment_rate"]
    lines = [
        "逐字稿自動 QC 摘要",
        "=" * 30,
        f"語言：{summary['language']}",
        f"音訊長度：{summary['duration_seconds']:.1f} 秒",
        f"Segments：{summary['segment_count']}",
        f"逐詞數量：{summary['word_count']}",
        f"平均 word probability：{avg_probability if avg_probability is not None else 'N/A'}",
        (
            f"低信心詞：{summary['low_probability_word_count']} "
            f"({low_rate:.1%})" if low_rate is not None else "低信心詞：N/A"
        ),
        (
            f"需要複查的 segments：{summary['review_segment_count']} "
            f"({review_rate:.1%})" if review_rate is not None else "需要複查的 segments：N/A"
        ),
        f"缺少／無效逐詞時間戳：{summary['missing_or_invalid_word_timestamp_count']}",
        "",
        "專有名詞檢查：",
    ]
    if summary["missing_reference_terms"]:
        lines.extend(f"- 未在逐字稿完整找到：{term}" for term in summary["missing_reference_terms"])
    else:
        lines.append("- metadata 中的參考詞均有出現")
    lines.extend([
        "",
        "提醒：此報告只能篩出疑似錯誤，不能取代人工校正與 CER/WER 評估。",
    ])
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {"json": json_path, "csv": csv_path, "summary": summary_path}


def generate_qc_report(raw_json_path: Path, metadata_path: Path | None = None) -> dict[str, Path]:
    raw_json_path = Path(raw_json_path)
    raw_data = load_json(raw_json_path)
    stem = raw_json_path.stem
    output_stem = stem[:-4] if stem.endswith("_raw") else stem
    output_prefix = raw_json_path.parent / output_stem
    report = analyze_transcript(raw_data, raw_json_path, metadata_path)
    return write_report_files(report, output_prefix)


def main() -> None:
    parser = argparse.ArgumentParser(description="產生逐字稿自動 QC report")
    parser.add_argument("raw_json", type=Path, help="Faster-Whisper 的 *_raw.json")
    parser.add_argument("--metadata", type=Path, help="選用的 *_metadata.json")
    args = parser.parse_args()

    metadata_path = args.metadata
    if metadata_path is None:
        candidate = args.raw_json.with_name(
            args.raw_json.name.replace("_raw.json", "_metadata.json")
        )
        metadata_path = candidate if candidate.exists() else None

    paths = generate_qc_report(args.raw_json, metadata_path)
    print("QC report 完成：")
    for path in paths.values():
        print(f"  - {path}")


if __name__ == "__main__":
    main()
