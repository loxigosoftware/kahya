"""Scheduler — the heartbeat.

Every tick:
1. v2: scan `scheduled_tasks` for due tasks and fire the owning amele
   with a generic trigger: `{"event": "time", "record_id": N}` — the
   amele reads its own record and decides (info, warning, MCP action,
   ask_confirm...). Success → status='success' + log; failure → up to
   3 attempts, then status='failed' + owner notification.
   Tasks never vanish silently.
2. v1 compatibility: item reminders (only items with an assigned amele;
     others are logged and skipped).

That is the whole job — Kahya is otherwise idle.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path

from .amele_runner import AmeleError, amele_yaml, run_amele
from .bot import TG
from .config import Config
from .db import KahyaDB
from .i18n import I18n

TICK_SECONDS = 60
MAX_ATTEMPTS = 3


def _in_quiet_hours(cfg: Config, now: datetime) -> bool:
    """True when `now` falls inside the quiet window (quiet_start–quiet_end)."""
    try:
        sh, sm = map(int, cfg.quiet_start.split(":"))
        eh, em = map(int, cfg.quiet_end.split(":"))
    except ValueError:
        return False
    cur = now.hour * 60 + now.minute
    s = sh * 60 + sm
    e = eh * 60 + em
    if s < e:  # e.g. 00:00–08:00
        return s <= cur < e
    return cur >= s or cur < e  # wraps midnight, e.g. 22:00–08:00


def _notify_failed(cfg: Config, slug: str, error: str) -> None:
    """Notify the user: task failed and was put on hold (§8)."""
    try:
        i18n = I18n(cfg.dir / "lang", cfg.language)
        msg = i18n.t("bot.task_failed", isim=slug, hata=str(error)[:200])
        TG(cfg.telegram_token).send(cfg.telegram_chat_id, msg, html=True)
    except Exception as e:  # noqa: BLE001 — bildirim asla tick'i düşürmesin
        print(f"  [ERROR] could not send notification: {e}")


def _run_due_tasks(cfg: Config, db: KahyaDB, quiet: bool,
                   now: datetime, dry_run: bool) -> list[dict]:
    """v2: scheduled_tasks scanner."""
    sent: list[dict] = []
    for task in db.due_scheduled_tasks(now.strftime("%Y-%m-%d %H:%M:%S")):
        amele = db.get_amele(task["amele_id"])
        record = {"task_id": task["id"], "amele": amele["slug"] if amele else "?",
                  "record_id": task["record_id"], "run_at": task["run_at"]}
        if not amele:
            db.set_task_status(task["id"], "failed")
            db.log("scheduler", {"event": "task_failed", **record, "error": "amele missing"})
            sent.append({**record, "sent": False, "error": "amele missing"})
            continue
        if quiet and not dry_run:
            db.log("scheduler", {"event": "task_held_quiet", **record})
            sent.append({**record, "held": True})
            continue

        yaml_path = amele_yaml(cfg, amele["slug"])
        task_msg = json.dumps({"event": "time", "record_id": task["record_id"]},
                              ensure_ascii=False)
        if dry_run:
            sent.append({**record, "dry": True})
            continue
        try:
            run_amele(cfg, yaml_path, task_msg, timeout_s=180)
            db.set_task_status(task["id"], "success")
            db.log("scheduler", {"event": "task_success", **record})
            sent.append({**record, "sent": True})
        except Exception as e:  # AmeleError, timeout, ...
            attempts = db.bump_task_attempt(task["id"], str(e))
            record["attempts"] = attempts
            db.log("scheduler", {"event": "task_retry", **record, "error": str(e)})
            if attempts >= MAX_ATTEMPTS:
                db.set_task_status(task["id"], "failed")
                db.log("scheduler", {"event": "task_failed", **record, "error": str(e)})
                _notify_failed(cfg, amele["slug"], str(e))
            sent.append({**record, "sent": False, "error": str(e)})
    return sent


def _run_legacy_items(cfg: Config, db: KahyaDB, today: date, quiet: bool,
                      now: datetime, dry_run: bool) -> list[dict]:
    """v1 compatibility: item reminders (legacy flow)."""
    sent: list[dict] = []
    for item in db.due_for_reminder(today):
        if db.reminders_sent_today(item["id"]):
            continue

        if quiet and not dry_run:
            db.log("scheduler", {"event": "reminder_held_quiet",
                                 "item_id": item["id"]})
            continue

        yaml_path = (amele_yaml(cfg, item["amele_slug"])
                     if item.get("amele_slug") else None)
        if yaml_path is None:
            db.log("scheduler", {"event": "reminder_skipped_no_amele",
                                 "item_id": item["id"]})
            continue

        task = (f"TASK event=time record_id={item['id']} "
                f"now={now.strftime('%Y-%m-%d %H:%M')}")
        record = {"item_id": item["id"], "amele": yaml_path.stem,
                  "due": item["due_date"]}

        if dry_run:
            record["dry"] = True
            sent.append(record)
            continue

        try:
            run_amele(cfg, yaml_path, task, timeout_s=180)
            db.mark_reminded(item["id"], item["due_date"])
            record["sent"] = True
            db.log("scheduler", {"event": "reminder_sent", **record})
        except Exception as e:  # AmeleError, timeout, ...
            record["sent"] = False
            record["error"] = str(e)
            db.log("scheduler", {"event": "reminder_failed", **record})
        sent.append(record)
    return sent


def tick(cfg: Config, db: KahyaDB, today: date | None = None,
         dry_run: bool = False, now: datetime | None = None) -> list[dict]:
    """Deliver due scheduled tasks + today's reminders."""
    today = today or date.today()
    now = now or datetime.now()
    quiet = _in_quiet_hours(cfg, now)
    sent = _run_due_tasks(cfg, db, quiet, now, dry_run)
    sent += _run_legacy_items(cfg, db, today, quiet, now, dry_run)
    return sent


def run_forever(cfg: Config, db: KahyaDB) -> None:
    print(f"[kahya scheduler] started at {datetime.now().isoformat(timespec='seconds')} "
          f"(tick {TICK_SECONDS}s) — db: {cfg.db_path}")
    while True:
        try:
            results = tick(cfg, db)
            for r in results:
                status = "sent" if r.get("sent") else ("dry" if r.get("dry")
                                                       else "FAILED")
                what = f"task {r['task_id']}" if "task_id" in r else f"item {r['item_id']}"
                print(f"  [{status}] {what} via {r['amele']}")
        except Exception as e:
            print(f"  [ERROR] tick failed: {e}")
        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    db = KahyaDB(Config().db_path)
    cfg = Config(db)
    try:
        if dry:
            results = tick(cfg, db, dry_run=True)
            print(f"[kahya scheduler] DRY RUN — {len(results)} item(s) due:")
            for r in results:
                if "task_id" in r:
                    print(f"  - task {r['task_id']} via {r['amele']} (record {r['record_id']})")
                else:
                    print(f"  - item {r['item_id']} via {r['amele']} (due {r['due']})")
            sys.exit(0)
        run_forever(cfg, db)
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        db.close()
