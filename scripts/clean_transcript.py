import json
import re
import sys
from pathlib import Path


# ============================================================
# 1. 贅詞設定
# ============================================================

FILLER_WORDS = {
    "uh",
    "um",
    "erm",
    "hmm",
    "mm",
    "mhm",
}


# 多字型口語填充詞
FILLER_PHRASES = [
    "you know",
    "you see",
]


# ============================================================
# 2. 清理單一文字
# ============================================================

def clean_text(text: str):
    """
    清理 Whisper 原始文字。

    原則：
    1. 移除明顯口語填充詞
    2. 保留具有語意或情緒意義的詞
    3. 不進行激進的句子重寫
    """

    if not text:
        return ""

    cleaned = text

    # --------------------------------------------------------
    # 移除多字型 filler
    # --------------------------------------------------------

    for phrase in FILLER_PHRASES:

        pattern = rf"\b{re.escape(phrase)}\b"

        cleaned = re.sub(
            pattern,
            "",
            cleaned,
            flags=re.IGNORECASE
        )

    # --------------------------------------------------------
    # 移除單字 filler
    # --------------------------------------------------------

    for word in FILLER_WORDS:

        pattern = rf"\b{re.escape(word)}\b"

        cleaned = re.sub(
            pattern,
            "",
            cleaned,
            flags=re.IGNORECASE
        )

    # --------------------------------------------------------
    # 清理多餘空白
    # --------------------------------------------------------

    cleaned = re.sub(
        r"\s+",
        " ",
        cleaned
    )

    # --------------------------------------------------------
    # 清理標點前空白
    # --------------------------------------------------------

    cleaned = re.sub(
        r"\s+([,.!?;:])",
        r"\1",
        cleaned
    )

    return cleaned.strip()


# ============================================================
# 3. 判斷某個 word 是否為 filler
# ============================================================

def is_filler_word(word: str):

    clean_word = word.strip().lower()

    # 移除標點
    clean_word = re.sub(
        r"^[^\w]+|[^\w]+$",
        "",
        clean_word
    )

    return clean_word in FILLER_WORDS


# ============================================================
# 4. 處理 Whisper segment
# ============================================================

def process_segment(segment):

    original_text = segment.get(
        "text",
        ""
    ).strip()

    words = segment.get(
        "words",
        []
    )

    # --------------------------------------------------------
    # 如果 Whisper 沒有 word timestamp
    # --------------------------------------------------------

    if not words:

        cleaned_text = clean_text(
            original_text
        )

        new_segment = {
            "id": segment.get("id"),
            "start": segment.get("start"),
            "end": segment.get("end"),
            "original_text": original_text,
            "clean_text": cleaned_text,
            "words": []
        }

        return new_segment

    # --------------------------------------------------------
    # 有 word timestamp
    # --------------------------------------------------------

    cleaned_words = []

    for word_info in words:

        word = word_info.get(
            "word",
            ""
        )

        if not word:
            continue

        # 判斷是否為 filler
        filler = is_filler_word(word)

        cleaned_words.append({
            "word": word.strip(),
            "start": word_info.get("start"),
            "end": word_info.get("end"),
            "removed": filler
        })

    # --------------------------------------------------------
    # 只使用沒有被刪除的 word 建立 Clean Text
    # --------------------------------------------------------

    remaining_words = [
        w["word"]
        for w in cleaned_words
        if not w["removed"]
    ]

    cleaned_text = " ".join(
        remaining_words
    )

    cleaned_text = clean_text(
        cleaned_text
    )

    # --------------------------------------------------------
    # 建立新的 segment
    # --------------------------------------------------------

    new_segment = {
        "id": segment.get("id"),
        "start": segment.get("start"),
        "end": segment.get("end"),

        "original_text": original_text,

        "clean_text": cleaned_text,

        "words": cleaned_words
    }

    return new_segment


# ============================================================
# 5. 處理整份 Raw JSON
# ============================================================

def clean_transcript(input_path, output_path):

    print("======================================")
    print("開始建立 Clean Transcript")
    print("======================================")

    print(f"📂 Raw JSON：{input_path}")

    # --------------------------------------------------------
    # 讀取 Raw JSON
    # --------------------------------------------------------

    with open(
        input_path,
        "r",
        encoding="utf-8"
    ) as f:

        data = json.load(f)

    # --------------------------------------------------------
    # 取得 segments
    # --------------------------------------------------------

    segments = data.get(
        "segments",
        []
    )

    print(
        f"🔍 找到 {len(segments)} 個 Whisper segments"
    )

    # --------------------------------------------------------
    # 處理所有 segments
    # --------------------------------------------------------

    cleaned_segments = []

    for segment in segments:

        cleaned_segment = process_segment(
            segment
        )

        cleaned_segments.append(
            cleaned_segment
        )

    # --------------------------------------------------------
    # 建立 Clean JSON
    # --------------------------------------------------------

    clean_data = {

        "source_file": str(input_path),

        "language": data.get(
            "language"
        ),

        "text": " ".join(
            segment["clean_text"]
            for segment in cleaned_segments
            if segment["clean_text"]
        ),

        "segments": cleaned_segments
    }

    # --------------------------------------------------------
    # 寫入 JSON
    # --------------------------------------------------------

    with open(
        output_path,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            clean_data,
            f,
            ensure_ascii=False,
            indent=2
        )

    print(
        f"✅ Clean JSON 完成：{output_path}"
    )


# ============================================================
# 6. 建立 Clean TXT
# ============================================================

def create_clean_txt(
    clean_json_path,
    txt_output_path
):

    with open(
        clean_json_path,
        "r",
        encoding="utf-8"
    ) as f:

        data = json.load(f)

    with open(
        txt_output_path,
        "w",
        encoding="utf-8"
    ) as f:

        for segment in data["segments"]:

            start = segment.get(
                "start",
                0
            )

            end = segment.get(
                "end",
                0
            )

            text = segment.get(
                "clean_text",
                ""
            )

            if not text:
                continue

            f.write(
                f"[{start:.3f} --> {end:.3f}] "
                f"{text}\n"
            )

    print(
        f"✅ Clean TXT 完成：{txt_output_path}"
    )


# ============================================================
# 7. 主程式
# ============================================================

def main():

    if len(sys.argv) < 2:

        print(
            "使用方法："
        )

        print(
            "python scripts/clean_transcript.py "
            "output/2454_20260311/2454_20260311_raw.json"
        )

        return

    input_path = Path(
        sys.argv[1]
    )

    if not input_path.exists():

        print(
            f"❌ 找不到檔案：{input_path}"
        )

        return

    # --------------------------------------------------------
    # output 檔案名稱
    # --------------------------------------------------------

    output_dir = input_path.parent

    call_id = input_path.stem.replace(
        "_raw",
        ""
    )

    clean_json_path = (
        output_dir
        / f"{call_id}_clean.json"
    )

    clean_txt_path = (
        output_dir
        / f"{call_id}_clean.txt"
    )

    # --------------------------------------------------------
    # 執行
    # --------------------------------------------------------

    clean_transcript(
        input_path,
        clean_json_path
    )

    create_clean_txt(
        clean_json_path,
        clean_txt_path
    )

    print()
    print("🎉 Clean Transcript 處理完成！")


if __name__ == "__main__":
    main()
