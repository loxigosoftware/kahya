#!/usr/bin/env python3
"""search_history — Kahya tool (subprocess). Full-text search in the
conversation archive.

stdin:  {"q": "köpek aşısı", "thread_id": "chat:42"}   (thread_id opsiyonel)
stdout: JSON — eşleşen konuşma mesajları (en yeni önce):
        [{thread_id, role, content, ts}] veya []

REDESIGN §3.5: "bunu konuşmuştuk" tarzı sorularda Kahya arşivi tarar ve
bulunan mesajları bağlama katar.

Env:    KAHYA_DB
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kahya.db import KahyaDB  # noqa: E402


def main():
    db_path = os.environ.get("KAHYA_DB", "")
    if not db_path:
        print(json.dumps({"error": "KAHYA_DB is not set"}))
        return 1
    try:
        req = json.loads(sys.stdin.read().strip() or "{}")
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"bad JSON: {e}"}))
        return 1

    q = (req.get("q") or "").strip()
    if not q:
        print(json.dumps({"error": "q gerekli"}))
        return 1

    db = KahyaDB(db_path)
    try:
        hits = db.search_messages(q, thread_id=req.get("thread_id"), limit=20)
        out = [{"thread_id": h["thread_id"], "role": h["role"],
                "content": h["content"], "ts": h["ts"]} for h in hits]
        print(json.dumps(out, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
