#!/usr/bin/env python3
"""find_ameles — Kahya tool (subprocess). Keyword search over the index.

stdin:  {"q": "mail hatırlatma"}
stdout: JSON — eşleşen amelesin kompakt listesi:
        [{id, slug, description, match_reason}] veya [] (eşleşme yok)

Fallback search when nothing matches the Kahya index;
sonuç yoksa Kahya kullanıcıya sorar.

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
        words = [w for w in q.lower().replace("amele", "").split() if w]
        rows = db.con.execute(
            "SELECT * FROM ameles WHERE enabled = 1 ORDER BY name").fetchall()
        out = []
        for r in rows:
            hay = " ".join([str(r["slug"]), str(r["name"]),
                            str(r["description"] or "")]).lower()
            hits = [w for w in words if w in hay]
            if hits:
                out.append({
                    "id": r["id"], "slug": r["slug"],
                    "description": r["description"],
                    "match_reason": ", ".join(hits),
                })
        print(json.dumps(out, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
