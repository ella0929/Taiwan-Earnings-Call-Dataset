"""Run only QA classification for an already imported call."""
import argparse
import asyncio
from qa_processor import process_qa_pipeline

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("call_id")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(process_qa_pipeline(args.call_id)))
