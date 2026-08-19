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

# --- tools ---
def tool(script, payload):
    r = subprocess.run([sys.executable, script], input=json.dumps(payload),
                       capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent / "tools"))
    return r.returncode, r.stdout.strip()

rc, out = tool("db_get.py", {"op": "item", "id": bill_id})
check("db_get item", rc == 0 and json.loads(out)["title"] == "Su faturası")
rc, out = tool("db_get.py", {"op": "agents"})
check("db_get agents", rc == 0 and len(json.loads(out)) == 2)
rc, out = tool("db_get.py", {"op": "items", "agent": "fatura", "status": "open"})
check("db_get items filtered", rc == 0 and all(i["agent_id"] == 1 for i in json.loads(out)))
rc, out = tool("db_put.py", {"op": "insert", "table": "items",
                             "data": {"title": "Aşı", "kind": "vaccination",
                                      "due_date": "2026-12-01"}})
new_id = json.loads(out)["id"] if rc == 0 else None
check("db_put insert", rc == 0 and new_id)
rc, out = tool("db_put.py", {"op": "update", "table": "items", "id": new_id,
                             "data": {"status": "done"}})
check("db_put update", rc == 0)
rc, out = tool("db_put.py", {"op": "insert", "table": "agents",
                             "data": {"slug": "hack", "name": "Hack"}})
check("db_put blocks agents table", rc == 1)
rc, out = tool("db_get.py", {"op": "nonsense"})
check("db_get unknown op", rc == 1)

print()
if fails:
    print(f"FAILED: {len(fails)} -> {fails}")
    sys.exit(1)
print("ALL TESTS PASSED")
