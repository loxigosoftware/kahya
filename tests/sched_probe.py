#!/usr/bin/env python3
"""Scheduler dry-run probe used by e2e test."""
import json
import sys
from datetime import date, timedelta

sys.path.insert(0, "/workspace/kahya")

from kahya.config import Config
from kahya.db import KahyaDB
from kahya.scheduler import tick

cfg = Config()
db = KahyaDB(cfg.db_path)
today = date.today()
iid = db.insert_item({
    "title": "Upcoming bill", "amount": 150, "currency": "TRY",
    "due_date": (today + timedelta(days=2)).isoformat(),
    "remind_before_days": 2,
})
res = tick(cfg, db, today=today, dry_run=True)
print(json.dumps({"window_item": any(r["item_id"] == iid and r.get("dry") for r in res)}))
