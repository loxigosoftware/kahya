#!/usr/bin/env python3
"""db_put — amele tool (subprocess). Write to the amele's own records.

stdin:  JSON request (records contract):
          {"op": "put", "data": {...}}            → yeni kayıt (kendi amele_id)
          {"op": "put", "id": 5, "data": {...}}   → güncelle (merge)
          {"op": "delete", "id": 5}               → sil
stdout: JSON — {"id": N} / {"deleted": true} veya {"error": "..."}

Kurallar:
- KAHYA_AMELE_ID env'i yazma için zorunludur — her kayıt bir ameleye aittir.
- Amele yalnız KENDİ kayıtlarına yazabilir (id'li işlemlerde amele_id kontrolü).
- Doğrulama: data geçerli bir JSON nesnesi olmalı; amelenin şeması
  (ameles.schema_json) varsa alan tipleri şemaya uymalı. Bozuk veri
  hiçbir koşulda DB'ye yazılmaz .

Env:    KAHYA_DB, KAHYA_AMELE_ID
"""
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kahya.db import KahyaDB  # noqa: E402

_TYPES = {"string": str, "number": (int, float), "bool": bool,
          "date": str, "json": (dict, list)}


def _amele_id() -> int:
    v = os.environ.get("KAHYA_AMELE_ID", "").strip()
    if not v or not v.isdigit():
        raise ValueError("KAHYA_AMELE_ID is not set — amele kayıt yazamaz")
    return int(v)


def _valid_date(value) -> bool:
    try:
        date.fromisoformat(str(value))
        return True
    except ValueError:
        return False


def validate_data(data: Any, schema: Optional[dict]) -> Optional[str]:
    """Şema doğrulaması — hata mesajı veya None (geçerli)."""
    if not isinstance(data, dict):
        return "data must be a JSON object"
    if not schema:
        return None
    fields = schema.get("fields") or []
    allowed = {f.get("name") for f in fields if isinstance(f, dict)}
    for f in fields:
        if not isinstance(f, dict):
            continue
        name, typ = f.get("name"), f.get("type")
        if not name or not typ:
            continue
        if name not in data:
            continue
        value = data[name]
        if typ == "date" and value is not None:
            if not _valid_date(value):
                return f"'{name}' geçerli bir tarih değil (YYYY-MM-DD): {value!r}"
        elif typ not in _TYPES:
            return f"şemada bilinmeyen tip: {typ!r}"
        elif value is not None and not isinstance(value, _TYPES[typ]):
            return f"'{name}' tipi {typ} olmalı, {type(value).__name__} geldi"
    if allowed:
        unknown = set(data) - allowed
        if unknown:
            return (f"şemada olmayan alan: {sorted(unknown)} — "
                    f"izin verilen: {sorted(allowed)}")
    return None


def main():
    db_path = os.environ.get("KAHYA_DB", "")
    if not db_path:
        print(json.dumps({"error": "KAHYA_DB is not set"}))
        return 1
    try:
        req = json.loads(sys.stdin.read().strip() or "{}")
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"bad JSON: {e}"}, ensure_ascii=False))
        return 1

    op = req.get("op")
    if op not in ("put", "delete"):
        print(json.dumps({"error": f"unknown op: {op!r} (beklenen: put, delete)"}, ensure_ascii=False))
        return 1

    db = KahyaDB(db_path)
    try:
        aid = _amele_id()
        amele = db.get_amele(aid)
        if not amele:
            print(json.dumps({"error": f"amele {aid} bulunamadı"}, ensure_ascii=False))
            return 1
        schema = None
        if amele.get("schema_json"):
            try:
                schema = json.loads(amele["schema_json"]) or None
            except (TypeError, json.JSONDecodeError):
                schema = None

        if op == "put":
            data = req.get("data")
            err = validate_data(data, schema)
            if err:
                print(json.dumps({"error": err}, ensure_ascii=False))
                return 1
            if "id" in req and req.get("id"):
                row = db.con.execute(
                    "SELECT amele_id FROM records WHERE id = ?",
                    (req["id"],)).fetchone()
                if not row:
                    print(json.dumps({"error": f"kayıt {req['id']} yok"}))
                    return 1
                if row["amele_id"] != aid:
                    print(json.dumps({"error": "başka amelenin kaydına yazılamaz"}, ensure_ascii=False))
                    return 1
                db.update_record(req["id"], data)
                print(json.dumps({"id": req["id"], "updated": True}))
            else:
                rid = db.add_record(aid, data)
                print(json.dumps({"id": rid}))
        elif op == "delete":
            rid = req.get("id")
            if not rid:
                print(json.dumps({"error": "delete needs id"}, ensure_ascii=False))
                return 1
            row = db.con.execute(
                "SELECT amele_id FROM records WHERE id = ?", (rid,)).fetchone()
            if not row:
                print(json.dumps({"error": f"kayıt {rid} yok"}, ensure_ascii=False))
                return 1
            if row["amele_id"] != aid:
                print(json.dumps({"error": "başka amelenin kaydı silinemez"}, ensure_ascii=False))
                return 1
            db.delete_record(rid)
            print(json.dumps({"deleted": True, "id": rid}))
    except ValueError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 1
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
