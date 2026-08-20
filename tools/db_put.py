#!/usr/bin/env python3
"""db_put — amele tool (subprocess). Write access to the Kahya database.

stdin:  JSON request, one of:
          {"op": "insert", "table": "items", "data": {...}, "agent_id": N}
          {"op": "update", "table": "items", "id": 5, "data": {...}}
stdout: JSON — {"id": <rowid>} or {"error": "..."}

Only the "items" table is writable from agents, and only for the columns
listed below — an agent can never touch agents, reminders or chat state.
(v1 uyumluluk sürümü — schema v2'de records'a yazar; Step 2'de tam yeniden
yazım + JSON doğrulama.)

Env:    KAHYA_DB (path to the SQLite file)
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kahya.db import KahyaDB  # noqa: E402

# columns an agent may write, with allowed types
WRITABLE = {
    "title": str, "kind": str, "amount": (int, float, type(None)),
    "currency": (str, type(None)), "due_date": (str, type(None)),
    "repeat_rule": str, "repeat_detail": (str, type(None)),
    "remind_before_days": int, "note": (str, type(None)),
    "status": str, "meta_json": (str, type(None)),
}


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

    op = req.get("op")
    table = req.get("table")
    if table != "items":
        print(json.dumps({"error": "only table 'items' is writable"}))
        return 1

    data = req.get("data") or {}
    # whitelist columns + types
    clean = {}
    for k, v in data.items():
        if k not in WRITABLE:
            continue
        if v is not None and not isinstance(v, WRITABLE[k]):
            print(json.dumps({"error": f"bad type for column {k}"}))
            return 1
        clean[k] = v
    if "status" not in clean:
        clean["status"] = "open"

    db = KahyaDB(db_path)
    try:
        if op == "insert":
            item_id = db.insert_item(clean, agent_id=req.get("agent_id"))
            print(json.dumps({"id": item_id}))
        elif op == "update":
            if not req.get("id"):
                print(json.dumps({"error": "update needs id"}))
                return 1
            db.update_item(req["id"], clean)
            print(json.dumps({"id": req["id"], "updated": True}))
        else:
            print(json.dumps({"error": f"unknown op: {op}"}))
            return 1
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
