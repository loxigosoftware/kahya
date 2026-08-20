#!/usr/bin/env python3
"""db_get — amele tool (subprocess). Read-only access to the Kahya database.

stdin:  JSON request, one of:
          {"op": "item",  "id": 5}                    → one item
          {"op": "items", "agent": "fatura", "status": "open"} → list
          {"op": "agents"}                            → known agents (ameleler)
          {"op": "reminders", "item_id": 5}           → sent reminders
stdout: JSON — the record(s), or {"error": "..."}
Env:    KAHYA_DB (path to the SQLite file)

v1 uyumluluk sürümü (schema v2 üzerinde çalışır) — Step 2'de tam yeniden yazım.
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
        print(json.dumps({"error": "KAHYA_DB is not set"}, ensure_ascii=False))
        return 1
    try:
        req = json.loads(sys.stdin.read().strip() or "{}")
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"bad JSON: {e}"}))
        return 1

    op = req.get("op")
    db = KahyaDB(db_path)

    try:
        if op == "item":
            print(json.dumps(db.get_item(req.get("id")), ensure_ascii=False))
        elif op == "items":
            print(json.dumps(
                db.list_items(agent_slug=req.get("agent"), status=req.get("status")),
                ensure_ascii=False))
        elif op == "agents":
            print(json.dumps(
                [{"id": a["id"], "slug": a["slug"], "name": a["name"],
                  "enabled": a["enabled"]} for a in db.list_agents()],
                ensure_ascii=False))
        elif op == "reminders":
            rows = db.con.execute(
                "SELECT payload FROM logs WHERE source = 'scheduler' "
                "AND json_extract(payload, '$.event') = 'reminder_sent' "
                "AND json_extract(payload, '$.item_id') = ? ORDER BY ts DESC",
                (req.get("item_id"),)).fetchall()
            print(json.dumps(
                [{"item_id": req.get("item_id"), "sent_on": r["ts"][:10]}
                 for r in rows], ensure_ascii=False))
        else:
            print(json.dumps({"error": f"unknown op: {op}"}))
            return 1
    except Exception as e:  # sqlite/JSON hataları dahil — amele hata görsün
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
