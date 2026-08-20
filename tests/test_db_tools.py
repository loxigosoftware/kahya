#!/usr/bin/env python3
"""Smoke test: db layer + tools."""
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kahya.db import KahyaDB  # noqa: E402

DB = Path(__file__).parent / "data" / "test.db"
if DB.exists():
    DB.unlink()
os.environ["KAHYA_DB"] = str(DB)

db = KahyaDB(DB)
fails = []


def check(name, cond, extra=""):
    status = "OK " if cond else "FAIL"
    print(f"  [{status}] {name} {extra}")
    if not cond:
        fails.append(name)


# --- agents ---
db.create_agent("fatura", "Fatura Takipçisi", "track bills", "agents/fatura.yaml")
db.create_agent("pets", "Evcil Hayvan Ajanı", "pet care", "agents/pets.yaml")
check("2 agents created", len(db.list_agents()) == 2)
check("get_agent_by_slug", db.get_agent_by_slug("fatura")["name"] == "Fatura Takipçisi")

# --- items ---
today = date.today()
due = (today + timedelta(days=2)).isoformat()
bill_id = db.insert_item({
    "title": "Su faturası", "kind": "bill", "amount": 3000, "currency": "TRY",
    "due_date": due, "repeat_rule": "monthly", "repeat_detail": "20",
    "remind_before_days": 2,
}, agent_id=1)
check("bill inserted", bill_id > 0)
got = db.get_item(bill_id)
check("bill fields", got["title"] == "Su faturası" and got["amount"] == 3000.0)

# --- reminder window ---
# due in 2 days, remind 2 before → today is exactly the window start
win = db.due_for_reminder(today)
check("inside window", any(i["id"] == bill_id for i in win))
# item due in 5 days, remind 2 → still outside (starts 3 days from now)
far = db.insert_item({"title": "Uzak", "due_date": (today + timedelta(days=5)).isoformat(),
                      "remind_before_days": 2}, agent_id=1)
check("outside window", all(i["id"] != far for i in db.due_for_reminder(today)))
# due today → inside
same_day = db.insert_item({"title": "Bugün", "due_date": today.isoformat(),
                           "remind_before_days": 2}, agent_id=1)
check("due today inside", any(i["id"] == same_day for i in db.due_for_reminder(today)))

# --- reminder dedup ---
check("first reminder recorded", db.mark_reminded(bill_id, due) is True)
check("same-day dedup", db.mark_reminded(bill_id, due) is False)
check("reminders_sent_today", db.reminders_sent_today(bill_id) is True)

# --- overdue ---
overdue = db.insert_item({"title": "Gecikmiş", "due_date": (today - timedelta(days=1)).isoformat()}, agent_id=1)
check("overdue included", any(i["id"] == overdue for i in db.due_for_reminder(today)))

# --- repeat roll ---
m = db.insert_item({"title": "Kira", "due_date": "2026-09-20", "repeat_rule": "monthly",
                    "repeat_detail": "20"}, agent_id=1)
done = db.complete_item(m)
check("monthly roll", done["rolled"] and done["due_date"] == "2026-10-20", done["due_date"])
y = db.insert_item({"title": "Sigorta", "due_date": "2026-08-19", "repeat_rule": "yearly",
                    "repeat_detail": "08-19"}, agent_id=1)
done_y = db.complete_item(y)
check("yearly roll", done_y["rolled"] and done_y["due_date"] == "2027-08-19", done_y["due_date"])
n = db.insert_item({"title": "Tek sefer", "due_date": "2026-08-20"}, agent_id=1)
done_n = db.complete_item(n)
check("one-off done", not done_n["rolled"] and done_n["status"] == "done")

# --- tools (records sözleşmesi — REDESIGN §2.3) ---
def tool(script, payload, amele_id=None):
    env = {**os.environ, "KAHYA_DB": str(DB)}
    if amele_id is not None:
        env["KAHYA_AMELE_ID"] = str(amele_id)
    r = subprocess.run([sys.executable, script], input=json.dumps(payload),
                       capture_output=True, text=True, env=env,
                       cwd=str(Path(__file__).resolve().parent.parent / "tools"))
    return r.returncode, r.stdout.strip()

# db_put: amele_id olmadan yazılamaz
rc, out = tool("db_put.py", {"op": "put", "data": {"ad": "X"}})
check("db_put needs amele_id", rc == 1 and "KAHYA_AMELE_ID" in out)
# db_put: put ile yeni kayıt (fatura amelesi = id 1)
rc, out = tool("db_put.py", {"op": "put", "data": {"ad": "Aşı", "tür": "saglik",
                                                   "due_date": "2026-12-01"}}, amele_id=1)
new_id = json.loads(out)["id"] if rc == 0 else None
check("db_put put insert", rc == 0 and new_id)
# db_get: get ile oku
rc, out = tool("db_get.py", {"op": "get", "id": new_id}, amele_id=1)
check("db_get get", rc == 0 and json.loads(out)["data"]["ad"] == "Aşı")
# db_put: update (merge)
rc, out = tool("db_put.py", {"op": "put", "id": new_id, "data": {"not": "yıllık"}},
               amele_id=1)
check("db_put put update", rc == 0 and json.loads(out).get("updated") is True)
# db_get: list yalnız kendi amelesinin kayıtları
rc, out = tool("db_get.py", {"op": "list"}, amele_id=1)
got = json.loads(out) if rc == 0 else []
check("db_get list own scope", rc == 0 and all(r["amele_id"] == 1 for r in got)
      and any(r["id"] == new_id for r in got))
rc, out = tool("db_get.py", {"op": "list"}, amele_id=2)
check("db_get list other scope", rc == 0 and json.loads(out) == [])
# db_get: search
rc, out = tool("db_get.py", {"op": "search", "q": "Aşı"}, amele_id=1)
check("db_get search", rc == 0 and any(r["id"] == new_id for r in json.loads(out)))
# db_put: başka amelenin kaydına yazılamaz
rc, out = tool("db_put.py", {"op": "put", "id": new_id, "data": {"ad": "Hack"}},
               amele_id=2)
check("db_put blocks other amele", rc == 1 and "yazılamaz" in out, out)
# db_put: delete
rc, out = tool("db_put.py", {"op": "delete", "id": new_id}, amele_id=1)
check("db_put delete", rc == 0 and json.loads(out).get("deleted") is True)
rc, out = tool("db_get.py", {"op": "get", "id": new_id}, amele_id=1)
check("db_get after delete", rc == 0 and json.loads(out) is None)
# eski v1 sözleşmesi öldü
rc, out = tool("db_put.py", {"op": "insert", "table": "items",
                             "data": {"title": "X"}}, amele_id=1)
check("v1 insert op rejected", rc == 1)
# şema doğrulama: şemalı amele bozuk veriyi reddeder
sema_id = db.create_amele("sema-amele", "Şemalı", "şema testi",
                          schema_json={"fields": [
                              {"name": "ad", "type": "string"},
                              {"name": "tarih", "type": "date"}]})
rc, out = tool("db_put.py", {"op": "put", "data": {"ad": "Tamam", "tarih": "2026-09-12"}},
               amele_id=sema_id)
check("schema ok", rc == 0)
rc, out = tool("db_put.py", {"op": "put", "data": {"ad": 42}}, amele_id=sema_id)
check("schema type rejected", rc == 1 and "tipi" in out)
rc, out = tool("db_put.py", {"op": "put", "data": {"ad": "X", "bilinmeyen": 1}},
               amele_id=sema_id)
check("schema unknown field rejected", rc == 1 and "şemada olmayan" in out, out)
rc, out = tool("db_put.py", {"op": "put", "data": {"ad": "X", "tarih": "12.09.2026"}},
               amele_id=sema_id)
check("schema bad date rejected", rc == 1 and "tarih" in out)

print()
if fails:
    print(f"FAILED: {len(fails)} -> {fails}")
    sys.exit(1)
print("ALL TESTS PASSED")
