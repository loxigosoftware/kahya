#!/usr/bin/env python3
"""db_get — amele tool (subprocess). Read-only access to the Kahya database.

stdin:  JSON request, one of:
          {"op": "item",  "id": 5}                    → one item
          {"op": "items", "agent": "fatura", "status": "open"} → list
          {"op": "agents"}                            → known agents
          {"op": "reminders", "item_id": 5}           → sent reminders
stdout: JSON — the record(s), or {"error": "..."}
Env:    KAHYA_DB (path to the SQLite file)
"""
import json
import os
import sqlite3
import sys


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

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    op = req.get("op")

    try:
        if op == "item":
            row = con.execute(
                "SELECT * FROM items WHERE id = ?", (req.get("id"),)
            ).fetchone()
            print(json.dumps(dict(row) if row else None, ensure_ascii=False))
        elif op == "items":
            q = "SELECT * FROM items WHERE 1=1"
            args = []
            if req.get("agent"):
                q += " AND agent_id = (SELECT id FROM agents WHERE slug = ?)"
                args.append(req["agent"])
            if req.get("status"):
                q += " AND status = ?"
                args.append(req["status"])
            q += " ORDER BY due_date, id"
            rows = con.execute(q, args).fetchall()
            print(json.dumps([dict(r) for r in rows], ensure_ascii=False))
        elif op == "agents":
            rows = con.execute(
                "SELECT id, slug, name, enabled FROM agents ORDER BY name"
            ).fetchall()
            print(json.dumps([dict(r) for r in rows], ensure_ascii=False))
        elif op == "reminders":
            rows = con.execute(
                "SELECT * FROM reminders WHERE item_id = ? ORDER BY sent_on DESC",
                (req.get("item_id"),),
            ).fetchall()
            print(json.dumps([dict(r) for r in rows], ensure_ascii=False))
        else:
            print(json.dumps({"error": f"unknown op: {op}"}))
            return 1
    except sqlite3.Error as e:
        print(json.dumps({"error": str(e)}))
        return 1
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
