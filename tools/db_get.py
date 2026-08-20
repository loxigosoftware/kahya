#!/usr/bin/env python3
"""db_get — amele tool (subprocess). Read records from the Kahya database.

stdin:  JSON request (records sözleşmesi — REDESIGN §2.3):
          {"op": "get", "id": 5}            → tek kayıt
          {"op": "list"}                    → kayıtlar (kendi amelesi)
          {"op": "search", "q": "bilet"}    → kayıtlarında metin arama
stdout: JSON — {"id", "amele_id", "data", "created_at", "updated_at"} veya
        liste; hata durumunda {"error": "..."}

Kapsam: KAHYA_AMELE_ID varsa yalnız o amelenin kayıtları görünür; yoksa
tümü (Kahya/orkestratör modu).

Env:    KAHYA_DB, (opsiyonel) KAHYA_AMELE_ID
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kahya.db import KahyaDB  # noqa: E402


def _row_out(r) -> dict:
    d = dict(r)
    d["data"] = json.loads(d.pop("data_json") or "{}")
    return d


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
    if op not in ("get", "list", "search"):
        print(json.dumps({"error": f"unknown op: {op!r} (beklenen: get, list, search)"}))
        return 1

    db = KahyaDB(db_path)
    try:
        aid = os.environ.get("KAHYA_AMELE_ID", "").strip()
        scope = f"AND r.amele_id = {int(aid)}" if aid.isdigit() else ""
        if op == "get":
            rid = req.get("id")
            if not rid:
                print(json.dumps({"error": "get needs id"}))
                return 1
            row = db.con.execute(
                f"SELECT * FROM records r WHERE r.id = ? {scope}", (rid,)).fetchone()
            print(json.dumps(_row_out(row) if row else None, ensure_ascii=False))
        elif op == "list":
            rows = db.con.execute(
                f"SELECT * FROM records r WHERE 1=1 {scope} ORDER BY r.id DESC"
            ).fetchall()
            print(json.dumps([_row_out(r) for r in rows], ensure_ascii=False))
        elif op == "search":
            q = req.get("q", "")
            if not q:
                print(json.dumps({"error": "search needs q"}))
                return 1
            rows = db.con.execute(
                f"SELECT * FROM records r WHERE r.data_json LIKE ? {scope} "
                "ORDER BY r.id DESC", (f"%{q}%",)).fetchall()
            print(json.dumps([_row_out(r) for r in rows], ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
