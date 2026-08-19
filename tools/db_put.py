#!/usr/bin/env python3
"""db_put — amele tool (subprocess). Write access to the Kahya database.

stdin:  JSON request, one of:
          {"op": "insert", "table": "items", "data": {...}}
          {"op": "update", "table": "items", "id": 5, "data": {...}}
stdout: JSON — {"id": <rowid>} or {"error": "..."}

Only the "items" table is writable from agents, and only for the columns
listed below — an agent can never touch agents, reminders or chat state.

Env:    KAHYA_DB (path to the SQLite file)
"""
import json
import os
import sqlite3
import sys

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

    con = sqlite3.connect(db_path)
    try:
        if op == "insert":
            cols = ", ".join(clean)
            marks = ", ".join("?" * len(clean))
            cur = con.execute(
                f"INSERT INTO items ({cols}) VALUES ({marks})",
                list(clean.values()),
            )
            con.commit()
            print(json.dumps({"id": cur.lastrowid}))
        elif op == "update":
            if not req.get("id"):
                print(json.dumps({"error": "update needs id"}))
                return 1
            sets = ", ".join(f"{k} = ?" for k in clean)
            con.execute(
                f"UPDATE items SET {sets} WHERE id = ?",
                list(clean.values()) + [req["id"]],
            )
            con.commit()
            print(json.dumps({"id": req["id"], "updated": con.total_changes > 0}))
        else:
            print(json.dumps({"error": f"unknown op: {op}"}))
            return 1
    except sqlite3.Error as e:
        con.rollback()
        print(json.dumps({"error": str(e)}))
        return 1
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
