"""Conservative, inspectable QA turn classification; no speaker-pair counting."""
import re
import unicodedata

OPEN = re.compile(r"(?:開放|开放|開始|开始|進入|进入).{0,8}(?:提問|提问|問答|问答)|q\s*&\s*a|open (?:the floor|for questions)|question.and.answer", re.I)
QUESTION = re.compile(r"請教|请教|請問|请问|想詢問|想询问|我的問題|我的问题|my (?:first |next |second )?question|could you|can you (?:explain|share|talk)|wondering if|想了解", re.I)
HOST = re.compile(r"下一位|下一个|下一個|請.{0,8}(?:發問|发问)|next question|next caller|turn (?:the call )?over", re.I)
CLOSE = re.compile(r"沒有其他問題|没有其他问题|沒有更多問題|no further questions|concludes? (?:our|the|today.s) (?:call|session)", re.I)


def classify_segments(segments, overrides=None):
    if not segments:
        raise ValueError("找不到可分類的逐字稿片段")
    overrides = overrides or {}
    known_ids = {s["segment_id"] for s in segments}
    if set(overrides) - known_ids:
        raise ValueError("QA overrides 含不存在的 segment_id")
    groups, issues = [], []
    in_qa, active_id, asker, answered = False, None, None, False
    number = 0
    for segment in segments:
        text = re.sub(r"\[\d+\.\d+\s*-->\s*\d+\.\d+\]", "", segment.get("raw_text") or "").strip()
        if not text:
            continue
        speaker = segment.get("speaker_id")
        if speaker is None:
            raise ValueError("講者尚未配對，不能執行 QA 分類")
        normalized = unicodedata.normalize("NFKC", text)
        manual = overrides.get(segment["segment_id"])
        if manual is not None and manual not in {"question", "answer", "moderator", "presentation"}:
            raise ValueError("QA override 只能是 question、answer、moderator、presentation")
        if manual:
            role = manual
        elif CLOSE.search(normalized):
            role = "presentation"
            in_qa, active_id, asker, answered = False, None, None, False
        elif (OPEN.search(normalized) or HOST.search(normalized)) and QUESTION.search(normalized):
            role = "unknown"
            in_qa = True
        elif OPEN.search(normalized) or HOST.search(normalized):
            role = "moderator"
        elif QUESTION.search(normalized):
            role = "question"
        elif in_qa and active_id:
            role = "question" if speaker == asker and not answered else "answer" if speaker != asker else "unknown"
        elif in_qa:
            role = "unknown"
        else:
            role = "presentation"
        if role == "moderator":
            in_qa = True
        elif role == "question":
            in_qa = True
            if active_id is None or answered or speaker != asker:
                number += 1
                active_id = f"Q_{number:02d}"
                asker, answered = speaker, False
        elif role == "answer":
            if not active_id:
                role = "unknown"
            else:
                answered = True
        elif manual == "presentation":
            in_qa, active_id, asker, answered = False, None, None, False
        question_id = active_id if role in {"question", "answer"} else None
        section = "qa" if in_qa else "presentation"
        if role == "unknown" or (role == "presentation" and "?" in normalized and not manual):
            issues.append({"segment_id": segment["segment_id"], "reason": "無法可靠判定提問／回答，請核對", "text": text})
        start, end = float(segment["start_time_sec"]), float(segment["end_time_sec"])
        if start < 0 or end < start:
            raise ValueError("逐字稿時間範圍無效")
        item = dict(segment, raw_text=text, transcript_text=text, section_type=section,
                    start_time_sec=start, end_time_sec=end,
                    question_id=question_id, role=role, classification_source="manual" if manual else "rule")
        item["source_segment_ids"] = [segment["segment_id"]]
        same = groups and all(groups[-1][key] == item[key] for key in ("speaker_id", "role", "question_id", "section_type", "classification_source"))
        if same:
            groups[-1]["transcript_text"] += " " + text
            groups[-1]["end_time_sec"] = item["end_time_sec"]
            groups[-1]["source_segment_ids"].append(segment["segment_id"])
        else:
            groups.append(item)
    if not groups:
        raise ValueError("逐字稿全部為空，保留原有 QA 資料")
    if in_qa and number == 0:
        issues.append({"reason": "已找到問答過場，但未辨識到提問"})
    question_ids = {g["question_id"] for g in groups if g["role"] == "question"}
    answered_ids = {g["question_id"] for g in groups if g["role"] == "answer"}
    for missing in sorted(question_ids - answered_ids):
        issues.append({"question_id": missing, "reason": "提問尚未配對到回答"})
    return groups, issues
