#!/usr/bin/env python3
"""Migration testi: sentetik v1 DB → migrate_v2 → veri bütünlüğü doğrulaması.

Veri kaybı olmadığını kanıtlar: agents→ameles, items→records (alan
eşlemesi dahil), reminders→silinir, korunan tablolar aynen kalır.
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "data" / "v1_fixture.db"
WORK = Path(__file__).parent / "data" / "mig_check.db"

V1_SCHEMA = """
CREATE TABLE agents (
  id INTEGER PRIMARY KEY, slug TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  role_prompt TEXT NOT NULL DEFAULT '', yaml_path TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE items (
  id INTEGER PRIMARY KEY, agent_id INTEGER REFERENCES agents(id),
  title TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'task', amount REAL,
  currency TEXT, due_date TEXT, repeat_rule TEXT NOT NULL DEFAULT 'none',
  repeat_detail TEXT, remind_before_days INTEGER NOT NULL DEFAULT 2,
  note TEXT, status TEXT NOT NULL DEFAULT 'open', meta_json TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE reminders (
  id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES items(id),
  due_date TEXT NOT NULL, sent_on TEXT NOT NULL DEFAULT (date('now')),
  channel TEXT NOT NULL DEFAULT 'telegram', UNIQUE(item_id, due_date, sent_on)
);
CREATE TABLE chat_state (chat_id INTEGER PRIMARY KEY, state_json TEXT NOT NULL);
CREATE TABLE logs (id INTEGER PRIMARY KEY, ts TEXT NOT NULL DEFAULT (datetime('now')),
  source TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}');
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE sessions (token TEXT PRIMARY KEY,
  created_at TEXT NOT NULL DEFAULT (datetime('now')), expires_at TEXT NOT NULL);
CREATE TABLE login_attempts (id INTEGER PRIMARY KEY,
  attempted TEXT NOT NULL DEFAULT (datetime('now')), success INTEGER NOT NULL DEFAULT 0);
"""


def build_fixture():
    if WORK.exists():
        WORK.unlink()
    con = sqlite3.connect(str(WORK))
    con.executescript(V1_SCHEMA)
    con.executemany(
        "INSERT INTO agents (id, slug, name, role_prompt, yaml_path) VALUES (?,?,?,?,?)",
        [(1, "fatura", "Fatura", "faturaları takip et", "agents/fatura.yaml"),
         (2, "pets", "Pets", "evcil hayvan takibi", "agents/pets.yaml")])
    con.executemany(
        """INSERT INTO items (id, agent_id, title, kind, amount, currency, due_date,
                             repeat_rule, repeat_detail, remind_before_days, note,
                             status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(1, 1, "Elektrik", "fatura", 3000, "TRY", "2026-09-20", "monthly", "20", 2,
          "son ödeme", "open", "2026-08-01 10:00:00"),
         (2, 1, "Su", "fatura", 850.5, "TRY", "2026-08-19", "none", None, 2,
          None, "open", "2026-08-10 09:00:00"),
         (3, None, "Bağsız", "task", None, None, "2026-08-25", "none", None, 0,
          "agentless", "open", "2026-08-15 08:30:00"),
         (4, 2, "Ödenmiş", "task", 100, "TL", "2026-07-01", "none", None, 0,
          None, "done", "2026-06-01 12:00:00")])
    con.executemany(
        "INSERT INTO reminders (item_id, due_date, sent_on) VALUES (?,?,?)",
        [(1, "2026-09-20", "2026-09-18"), (2, "2026-08-19", "2026-08-17")])
    con.executemany("INSERT INTO settings (key, value) VALUES (?,?)",
                    [("llm_model", "qwen3:27b"), ("lang", "tr")])
    con.execute("INSERT INTO logs (source, payload) VALUES ('test', '{}')")
    con.commit()
    con.close()


def main():
    build_fixture()
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "migrate_v2.py"),
                        str(WORK)], capture_output=True, text=True)
    print(r.stdout)
    fails = []

    def check(name, cond):
        print(f"  [{'OK ' if cond else 'FAIL'}] {name}")
        if not cond:
            fails.append(name)

    con = sqlite3.connect(str(WORK))
    con.row_factory = sqlite3.Row

    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    check("v1 tabloları kaldırıldı", not (tables & {"items", "reminders", "agents"}))
    check("korunan tablolar duruyor",
          {"chat_state", "logs", "settings", "sessions", "login_attempts"} <= tables)

    n_amele = con.execute("SELECT COUNT(*) FROM ameles").fetchone()[0]
    check("ameles: 2 + 1 (v1 fallback)", n_amele == 3)
    desc = con.execute("SELECT description FROM ameles WHERE slug='fatura'").fetchone()[0]
    check("role_prompt → description", desc == "faturaları takip et")
    model = con.execute("SELECT model_kind, model_name FROM ameles WHERE slug='pets'").fetchone()
    check("model defaultları", model[0] == "local" and model[1] == "qwen3:27b")

    n_rec = con.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    check("records: 4 item → 4 kayıt", n_rec == 4)
    r1 = con.execute("SELECT data_json FROM records WHERE id=1").fetchone()[0]
    d1 = json.loads(r1)
    check("alan eşlemesi (ad/tutar/tür)",
          d1["ad"] == "Elektrik" and d1["tutar"] == "3000.0 TRY" and d1["tür"] == "fatura")
    check("v1 alanları korundu (status/repeat)",
          d1["status"] == "open" and d1["repeat_rule"] == "monthly")
    r3 = con.execute("SELECT amele_id FROM records WHERE id=3").fetchone()[0]
    v1_id = con.execute("SELECT id FROM ameles WHERE slug='v1'").fetchone()[0]
    check("agentless item → v1 amelesi", r3 == v1_id)
    check("reminders verisi yok (tablo kaldırıldı)",
          "reminders" not in tables)
    check("settings korundu", con.execute(
        "SELECT COUNT(*) FROM settings").fetchone()[0] == 2)
    check("logs korundu", con.execute(
        "SELECT COUNT(*) FROM logs").fetchone()[0] == 1)
    check("yeni tablolar hazır",
          {"mcp_servers", "amele_mcp", "pending_actions", "scheduled_tasks",
           "conversation_messages", "conversation_fts"} <= tables)
    con.close()

    # idempotence
    r2 = subprocess.run([sys.executable, str(ROOT / "scripts" / "migrate_v2.py"),
                         str(WORK)], capture_output=True, text=True)
    check("idempotent (ikinci çalıştırma)", "zaten v2" in r2.stdout and r2.returncode == 0)

    print()
    if fails:
        print(f"FAILED: {len(fails)} -> {fails}")
        sys.exit(1)
    print("MIGRATION OK")


if __name__ == "__main__":
    main()
