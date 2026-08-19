"""Scheduler — the heartbeat.

Every tick: find open items inside their reminder window, spawn the
owning agent (or the general reminder agent) via amele to deliver ONE
reminder, record it. That is the whole job — Kahya is otherwise idle,
"50 configured agents, 0 running".
"""
from __future__ import annotations

import time
from datetime import date, datetime
from pathlib import Path

from .amele_runner import AmeleError, agent_yaml, run_agent
from .config import Config
from .db import KahyaDB

TICK_SECONDS = 60


def tick(cfg: Config, db: KahyaDB, today: date | None = None,
         dry_run: bool = False) -> list[dict]:
    """Deliver today's reminders. Returns what was sent (or would be)."""
    today = today or date.today()
    sent: list[dict] = []
    for item in db.due_for_reminder(today):
        if db.reminders_sent_today(item["id"]):
            continue

        yaml_path: Path | None = None
        if item.get("agent_slug"):
            yaml_path = agent_yaml(cfg, item["agent_slug"])
        if yaml_path is None:
            yaml_path = cfg.agents_dir / "reminder.yaml"

        task = (f"REMINDER item_id={item['id']} due={item['due_date']} "
                f"today={today.isoformat()}")
        record = {"item_id": item["id"], "agent": yaml_path.stem,
                  "due": item["due_date"]}

        if dry_run:
            record["dry"] = True
            sent.append(record)
            continue

        try:
            run_agent(cfg, yaml_path, task, timeout_s=180)
            db.mark_reminded(item["id"], item["due_date"])
            record["sent"] = True
            db.log("scheduler", {"event": "reminder_sent", **record})
        except AmeleError as e:
            record["sent"] = False
            record["error"] = str(e)
            db.log("scheduler", {"event": "reminder_failed", **record})
        except Exception as e:  # timeout etc.
            record["sent"] = False
            record["error"] = str(e)
            db.log("scheduler", {"event": "reminder_failed", **record})
        sent.append(record)
    return sent


def run_forever(cfg: Config, db: KahyaDB) -> None:
    print(f"[kahya scheduler] started at {datetime.now().isoformat(timespec='seconds')} "
          f"(tick {TICK_SECONDS}s) — db: {cfg.db_path}")
    while True:
        try:
            results = tick(cfg, db)
            for r in results:
                status = "sent" if r.get("sent") else ("dry" if r.get("dry") else "FAILED")
                print(f"  [{status}] item {r['item_id']} via {r['agent']}")
        except Exception as e:
            print(f"  [ERROR] tick failed: {e}")
        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    cfg = Config()
    db = KahyaDB(cfg.db_path)
    try:
        if dry:
            results = tick(cfg, db, dry_run=True)
            print(f"[kahya scheduler] DRY RUN — {len(results)} reminder(s) would be sent today:")
            for r in results:
                print(f"  - item {r['item_id']} via {r['agent']} (due {r['due']})")
            sys.exit(0)
        run_forever(cfg, db)
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        db.close()
