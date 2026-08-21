#!/usr/bin/env python3
"""get_amele_profile — Kahya tool (subprocess). Full amele definition.

stdin:  {"amele_id": 3} veya {"slug": "mail-amele"}
stdout: JSON — amelenin tam tanımı:
        {id, slug, name, description, schema, model_kind, model_name,
         mcp_servers: [{name, kind, url/command, tools_include}]}
        Hata: {"error": "..."}

Kahya picks the target from the index and only that full definition is
çeker — bağlam şişmez.

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

    db = KahyaDB(db_path)
    try:
        if "amele_id" in req:
            amele = db.get_amele(req["amele_id"])
        elif req.get("slug"):
            amele = db.get_amele_by_slug(req["slug"])
        else:
            print(json.dumps({"error": "amele_id veya slug gerekli"}))
            return 1
        if not amele:
            print(json.dumps({"error": f"amele bulunamadı: {req}"}, ensure_ascii=False))
            return 1
        schema = None
        if amele.get("schema_json"):
            try:
                schema = json.loads(amele["schema_json"]) or None
            except (TypeError, json.JSONDecodeError):
                schema = None
        mcp = [{"name": s["name"], "kind": s["kind"],
                "url": s.get("url"), "command": s.get("command"),
                "tools_include": s.get("tools_include")}
               for s in db.list_amele_mcp(amele["id"])]
        out = {
            "id": amele["id"], "slug": amele["slug"], "name": amele["name"],
            "description": amele.get("description", ""),
            "schema": schema,
            "model_kind": amele.get("model_kind", "local"),
            "model_name": amele.get("model_name"),
            "enabled": amele.get("enabled", 1),
            "mcp_servers": mcp,
        }
        print(json.dumps(out, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
