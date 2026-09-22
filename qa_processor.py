"""Classify QA and atomically replace one call's speech segments."""
import os
import json
import aiomysql
from dotenv import load_dotenv
from scripts.pipeline_common import ROOT, save_json
from scripts.qa_rules import classify_segments

load_dotenv(ROOT / ".env")


async def replace_segments(conn, call_id, segments):
    if not segments:
        raise ValueError("拒絕以空資料覆蓋 QA 結果")
    async with conn.cursor() as cur:
        await cur.execute("""SELECT ENGINE FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'speech_segments'""")
        row = await cur.fetchone()
        if not row or str(row[0]).upper() != "INNODB":
            raise RuntimeError("speech_segments 必須使用 InnoDB 才能保證交易回復；尚未修改資料")
    await conn.begin()
    try:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM speech_segments WHERE call_id = %s", (call_id,))
            await cur.executemany("""
                INSERT INTO speech_segments
                (segment_id, call_id, speaker_id, section_type, question_id,
                 start_time_sec, end_time_sec, transcript_text)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """, [(s['segment_id'], call_id, s['speaker_id'], s['section_type'], s['question_id'],
                    s['start_time_sec'], s['end_time_sec'], s['transcript_text']) for s in segments])
        await conn.commit()
    except BaseException:
        await conn.rollback()
        raise


async def process_qa_pipeline(call_id):
    conn = await aiomysql.connect(
        host=os.getenv("DB_HOST"), user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"),
        db=os.getenv("DB_NAME"), port=int(os.getenv("DB_PORT", "3306")), autocommit=False)
    try:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("""SELECT segment_id, call_id, speaker_id, raw_text, start_time_sec, end_time_sec
                FROM transcript_segments WHERE call_id=%s ORDER BY start_time_sec, segment_id""", (call_id,))
            raw = await cur.fetchall()
        folder = ROOT / "output" / call_id
        override_path = folder / f"{call_id}_qa_overrides.json"
        overrides = json.loads(override_path.read_text(encoding="utf-8-sig")) if override_path.exists() else {}
        groups, issues = classify_segments(raw, overrides)
        report = {"method": "rule_based", "status": "review_required" if issues else "classified",
                  "important_note": "規則推測，非人工驗證的問答標籤。", "issues": issues,
                  "segments": groups, "database_updated": False}
        report_path = folder / f"{call_id}_qa_review.json"
        save_json(report_path, report)
        if issues:
            print(f"QA 有 {len(issues)} 項待確認，保留原有 speech_segments。詳見 {report_path}")
            return 2
        await replace_segments(conn, call_id, groups)
        report["database_updated"] = True
        save_json(report_path, report)
        print(f"QA 分類完成，已寫入 {len(groups)} 個段落；規則判斷结果可於 {report_path} 核對。")
        return 0
    finally:
        conn.close()
        await conn.ensure_closed()
