"""SQLite layer — schema, CRUD and scheduler queries.

One file, zero setup: `data/kahya.db`. Backup = copy the file.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
  id          INTEGER PRIMARY KEY,
  slug        TEXT UNIQUE NOT NULL,
  name        TEXT NOT NULL,
  role_prompt TEXT NOT NULL DEFAULT '',
  yaml_path   TEXT NOT NULL DEFAULT '',
  enabled     INTEGER NOT NULL DEFAULT 1,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS items (
  id                 INTEGER PRIMARY KEY,
  agent_id           INTEGER REFERENCES agents(id),
  title              TEXT NOT NULL,
  kind               TEXT NOT NULL DEFAULT 'task',
  amount             REAL,
  currency           TEXT,
  due_date           TEXT,
  repeat_rule        TEXT NOT NULL DEFAULT 'none',
  repeat_detail      TEXT,
  remind_before_days INTEGER NOT NULL DEFAULT 2,
  note               TEXT,
  status             TEXT NOT NULL DEFAULT 'open',
  meta_json          TEXT,
  created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reminders (
  id       INTEGER PRIMARY KEY,
  item_id  INTEGER NOT NULL REFERENCES items(id),
  due_date TEXT NOT NULL,
  sent_on  TEXT NOT NULL DEFAULT (date('now')),
  channel  TEXT NOT NULL DEFAULT 'telegram',
  UNIQUE(item_id, due_date, sent_on)
);

CREATE TABLE IF NOT EXISTS chat_state (
  chat_id   INTEGER PRIMARY KEY,
  state_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS logs (
  id      INTEGER PRIMARY KEY,
  ts      TEXT NOT NULL DEFAULT (datetime('now')),
  source  TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS settings (
  key        TEXT PRIMARY KEY,
  value      TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
  token      TEXT PRIMARY KEY,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_attempts (
  id         INTEGER PRIMARY KEY,
  attempted  TEXT NOT NULL DEFAULT (datetime('now')),
  success    INTEGER NOT NULL DEFAULT 0
);
"""


class KahyaDB:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        self.con.commit()

    def close(self) -> None:
        self.con.close()

    # ---------- agents ----------

    def list_agents(self) -> list[dict]:
        rows = self.con.execute("SELECT * FROM agents ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def get_agent_by_slug(self, slug: str) -> Optional[dict]:
        row = self.con.execute("SELECT * FROM agents WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else None

    def create_agent(self, slug: str, name: str, role_prompt: str,
                     yaml_path: str = "") -> int:
        cur = self.con.execute(
            "INSERT INTO agents (slug, name, role_prompt, yaml_path) VALUES (?, ?, ?, ?)",
            (slug, name, role_prompt, yaml_path),
        )
        self.con.commit()
        return cur.lastrowid

    # ---------- items ----------

    @staticmethod
    def normalize_date(value) -> Optional[str]:
        """Coerce a due_date to YYYY-MM-DD, or None when unparseable.

        The LLM can return sloppy dates ("20/08/2026", "20.08.2026",
        "2026-8-2"); SQLite's date() and the panel's comparisons only
        understand ISO, so every write goes through this.
        """
        if not value:
            return None
        s = str(value).strip()
        if not s:
            return None
        try:
            return date.fromisoformat(s).isoformat()
        except ValueError:
            pass
        m = re.match(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})$", s)
        if m:
            d, mo, y = m.groups()
            y = f"20{y}" if len(y) == 2 else y
            try:
                return date(int(y), int(mo), int(d)).isoformat()
            except ValueError:
                return None
        m = re.match(r"^(\d{4})[./-](\d{1,2})[./-](\d{1,2})$", s)
        if m:
            y, mo, d = m.groups()
            try:
                return date(int(y), int(mo), int(d)).isoformat()
            except ValueError:
                return None
        return None

    def insert_item(self, data: dict, agent_id: Optional[int] = None) -> int:
        if agent_id is not None:
            data = {**data, "agent_id": agent_id}
        if data.get("due_date"):
            data = {**data, "due_date": self.normalize_date(data["due_date"])}
        if data.get("amount") in ("", None):
            data = {**data, "amount": None}
        cols = [k for k in data if k in (
            "agent_id", "title", "kind", "amount", "currency", "due_date",
            "repeat_rule", "repeat_detail", "remind_before_days", "note",
            "status", "meta_json")]
        cur = self.con.execute(
            f"INSERT INTO items ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [data[c] for c in cols],
        )
        self.con.commit()
        return cur.lastrowid

    def get_item(self, item_id: int) -> Optional[dict]:
        row = self.con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None

    def update_item(self, item_id: int, data: dict) -> None:
        cols = [k for k in data if k in (
            "title", "kind", "amount", "currency", "due_date",
            "repeat_rule", "repeat_detail", "remind_before_days", "note",
            "status", "meta_json")]
        if not cols:
            return
        self.con.execute(
            f"UPDATE items SET {', '.join(f'{c} = ?' for c in cols)} WHERE id = ?",
            [data[c] for c in cols] + [item_id],
        )
        self.con.commit()

    def list_items(self, agent_slug: Optional[str] = None,
                   status: Optional[str] = None) -> list[dict]:
        q = ("SELECT i.*, a.slug AS agent_slug, a.name AS agent_name "
             "FROM items i LEFT JOIN agents a ON a.id = i.agent_id WHERE 1=1")
        args: list[Any] = []
        if agent_slug:
            q += " AND a.slug = ?"
            args.append(agent_slug)
        if status:
            q += " AND i.status = ?"
            args.append(status)
        q += " ORDER BY i.due_date, i.id"
        rows = self.con.execute(q, args).fetchall()
        return [dict(r) for r in rows]

    # ---------- reminders / scheduler ----------

    def mark_reminded(self, item_id: int, due_date: str) -> bool:
        """Record that a reminder went out today for this item+due.

        UNIQUE(item_id, due_date, sent_on) makes the same-day reminder a
        no-op — the scheduler can run every minute without spamming.
        """
        try:
            self.con.execute(
                "INSERT INTO reminders (item_id, due_date) VALUES (?, ?)",
                (item_id, due_date),
            )
            self.con.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def reminders_sent_today(self, item_id: int) -> bool:
        row = self.con.execute(
            "SELECT 1 FROM reminders WHERE item_id = ? AND sent_on = date('now')",
            (item_id,),
        ).fetchone()
        return row is not None

    def due_for_reminder(self, today: date) -> list[dict]:
        """Open items whose reminder window includes today.

        Window: due_date - remind_before_days <= today <= due_date
        (e.g. a bill due on the 20th with remind_before_days=2 is
        reminded on the 18th, 19th and 20th), plus overdue items
        (due_date < today) — the owner keeps getting one reminder per
        day until they mark the item done.
        """
        rows = self.con.execute(
            """SELECT i.*, a.slug AS agent_slug, a.yaml_path
               FROM items i LEFT JOIN agents a ON a.id = i.agent_id
               WHERE i.status = 'open' AND i.due_date IS NOT NULL
                 AND ( (date(i.due_date) >= date(?)
                        AND date(i.due_date) <= date(?, '+' || i.remind_before_days || ' days'))
                       OR date(i.due_date) < date(?) )
               ORDER BY i.due_date""",
            (today.isoformat(), today.isoformat(), today.isoformat()),
        ).fetchall()
        return [dict(r) for r in rows]

    def next_due_date(self, item: dict) -> Optional[str]:
        """Compute the next occurrence after the current due_date."""
        rule = item.get("repeat_rule")
        detail = item.get("repeat_detail")
        cur = date.fromisoformat(item["due_date"])
        if rule == "daily":
            return (cur + timedelta(days=1)).isoformat()
        if rule == "weekly":
            # detail: weekday name ("monday"); fall back to +7 days
            target = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                      "friday": 4, "saturday": 5, "sunday": 6}.get(
                (detail or "").lower())
            if target is None:
                return (cur + timedelta(days=7)).isoformat()
            days_ahead = (target - cur.weekday()) % 7
            days_ahead = days_ahead or 7
            return (cur + timedelta(days=days_ahead)).isoformat()
        if rule == "monthly":
            # detail: day of month ("20"); fall back to same day next month
            day = int(detail) if detail and detail.isdigit() else cur.day
            month = cur.month + 1
            year = cur.year + (month - 1) // 12
            month = (month - 1) % 12 + 1
            import calendar
            day = min(day, calendar.monthrange(year, month)[1])
            return date(year, month, day).isoformat()
        if rule == "yearly":
            # detail: "MM-DD"; fall back to same month/day next year
            if detail and len(detail) == 5 and detail[2] == "-":
                mm, dd = int(detail[:2]), int(detail[3:])
            else:
                mm, dd = cur.month, cur.day
            year = cur.year + 1
            import calendar
            dd = min(dd, calendar.monthrange(year, mm)[1])
            return date(year, mm, dd).isoformat()
        return None

    def complete_item(self, item_id: int) -> Optional[dict]:
        """Mark done; if it repeats, roll the due date forward instead."""
        item = self.get_item(item_id)
        if not item:
            return None
        nxt = self.next_due_date(item)
        if nxt:
            self.update_item(item_id, {"due_date": nxt, "status": "open"})
            self.con.execute("DELETE FROM reminders WHERE item_id = ?", (item_id,))
            self.con.commit()
            return {**item, "due_date": nxt, "rolled": True}
        self.update_item(item_id, {"status": "done"})
        return {**item, "status": "done", "rolled": False}

    # ---------- chat state (bot conversation) ----------

    def get_chat_state(self, chat_id: int) -> dict:
        row = self.con.execute(
            "SELECT state_json FROM chat_state WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return json.loads(row["state_json"]) if row else {}

    def set_chat_state(self, chat_id: int, state: dict) -> None:
        self.con.execute(
            "INSERT INTO chat_state (chat_id, state_json) VALUES (?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET state_json = excluded.state_json",
            (chat_id, json.dumps(state, ensure_ascii=False)),
        )
        self.con.commit()

    # ---------- settings (panel-editable, env as fallback) ----------

    def get_setting(self, key: str) -> Optional[str]:
        row = self.con.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: Optional[str]) -> None:
        if value is None:
            self.con.execute("DELETE FROM settings WHERE key = ?", (key,))
        else:
            self.con.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now')) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (key, value),
            )
        self.con.commit()

    def all_settings(self) -> dict:
        rows = self.con.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}

    # ---------- sessions / login attempts ----------

    def create_session(self, token: str, ttl_hours: int = 24) -> None:
        self.con.execute(
            "DELETE FROM sessions WHERE expires_at < datetime('now')")
        self.con.execute(
            "INSERT INTO sessions (token, expires_at) VALUES (?, datetime('now', ?))",
            (token, f"+{ttl_hours} hours"),
        )
        self.con.commit()

    def session_valid(self, token: str) -> bool:
        row = self.con.execute(
            "SELECT 1 FROM sessions WHERE token = ? AND expires_at > datetime('now')",
            (token,)).fetchone()
        return row is not None

    def delete_session(self, token: str) -> None:
        self.con.execute("DELETE FROM sessions WHERE token = ?", (token,))
        self.con.commit()

    def failed_logins(self, since_minutes: int = 10) -> int:
        row = self.con.execute(
            "SELECT COUNT(*) AS n FROM login_attempts "
            "WHERE success = 0 AND attempted > datetime('now', ?)",
            (f"-{since_minutes} minutes",)).fetchone()
        return row["n"]

    def record_login(self, success: bool) -> None:
        self.con.execute(
            "INSERT INTO login_attempts (success) VALUES (?)", (1 if success else 0,))
        # keep the table small
        self.con.execute(
            "DELETE FROM login_attempts WHERE id NOT IN "
            "(SELECT id FROM login_attempts ORDER BY id DESC LIMIT 500)")
        self.con.commit()

    # ---------- logs ----------

    def log(self, source: str, payload: dict) -> None:
        self.con.execute(
            "INSERT INTO logs (source, payload) VALUES (?, ?)",
            (source, json.dumps(payload, ensure_ascii=False)),
        )
        self.con.commit()
