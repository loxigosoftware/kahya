#!/usr/bin/env python3
"""v1 → v2 schema migration for Kahya .

Usage:
    python3 scripts/migrate_v2.py <db_path> [--force]

- Creates the v2 tables, moves data (agents→ameles, items→records),
  drops the removed v1 tables (items, reminders).
- Runs inside a transaction: on error the DB is left untouched.
- Idempotent: if the DB is already v2 it prints the current state and exits.
- NEVER runs against the live DB blindly — test on a backup copy first.

Data mapping :
    agents   → ameles   (role_prompt → description; model defaults)
    items    → records    (title→ad, amount/currency→tutar, due_date,
                           note→not, kind→tür; v1 alanları data_json'da korunur)
    reminders→ dropped    (geçmiş logs'ta zaten var)
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kahya.db import SCHEMA  # noqa: E402  (v2 şeması — tek kaynak)


def _table_names(con: sqlite3.Connection) -> set[str]:
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {r[0] for r in rows}


def _v1_count(con: sqlite3.Connection, table: str) -> int:
    if table not in _table_names(con):
        return 0
    return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _fallback_amele(con: sqlite3.Connection) -> int:
    """agentless v1 item'ları için 'v1' amelesini bul/oluştur."""
    row = con.execute("SELECT id FROM ameles WHERE slug = 'v1'").fetchone()
    if row:
        return row[0]
    cur = con.execute(
        "INSERT INTO ameles (slug, name, description, yaml_path) "
        "VALUES ('v1', 'v1 (taşınan kayıtlar)', "
        "'v1''den agentless taşınan kayıtlar', '')")
    return cur.lastrowid


def migrate(db_path: str, force: bool = False) -> int:
    path = Path(db_path)
    if not path.exists():
        print(f"HATA: {path} yok")
        return 2

    con = sqlite3.connect(str(path), timeout=10)
    try:
        tables = _table_names(con)
        already_v2 = "ameles" in tables and "items" not in tables
        if already_v2 and not force:
            print(f"[{path.name}] zaten v2 — mevcut durum:")
            for t in ("ameles", "records", "mcp_servers", "amele_mcp",
                      "pending_actions", "scheduled_tasks", "conversation_messages"):
                print(f"  {t}: {_v1_count(con, t)}")
            return 0

        n_agents = _v1_count(con, "agents")
        n_items = _v1_count(con, "items")
        n_reminders = _v1_count(con, "reminders")
        print(f"[{path.name}] v1 durumu: agents={n_agents} items={n_items} "
              f"reminders={n_reminders}")

        con.execute("BEGIN IMMEDIATE")

        # 1) v2 şeması (yeni tablolar; mevcutlar korunur)
        con.executescript(SCHEMA)
        # FTS5 (yoksa atlanır — db.py LIKE fallback kullanır)
        try:
            con.executescript(
                "CREATE VIRTUAL TABLE IF NOT EXISTS conversation_fts USING fts5("
                "content, thread_id UNINDEXED)")
        except sqlite3.OperationalError as e:
            print(f"  NOT: FTS5 kurulamadı ({e}) — LIKE fallback kullanılacak")

        # 2) agents → ameles
        if "agents" in tables:
            con.execute(
                """INSERT INTO ameles (id, slug, name, description, yaml_path,
                                         model_kind, model_name, model_cfg,
                                         enabled, created_at)
                   SELECT id, slug, name, role_prompt, yaml_path,
                          'local', 'qwen3:27b', NULL, enabled, created_at
                   FROM agents""")

        # 3) items → records
        if "items" in tables:
            orphan = con.execute(
                "SELECT COUNT(*) FROM items WHERE agent_id IS NULL").fetchone()[0]
            fallback_id = _fallback_amele(con) if orphan else None
            if orphan:
                print(f"  NOT: {orphan} item agentless → 'v1' amelesine bağlanıyor")
            cur = con.execute("SELECT * FROM items")
            cols = [c[0] for c in cur.description]
            rows = cur.fetchall()
            for r in rows:
                d = dict(zip(cols, r))
                amount, currency = d.get("amount"), d.get("currency")
                tutar = ""
                if amount is not None and amount != "":
                    tutar = f"{amount}"
                    if currency:
                        tutar += f" {currency}"
                data = {
                    "ad": d.get("title"),
                    "tür": d.get("kind", "task"),
                    "tutar": tutar,
                    "due_date": d.get("due_date"),
                    "not": d.get("note"),
                }
                for k in ("repeat_rule", "repeat_detail", "remind_before_days",
                          "status", "meta_json", "amount", "currency", "note"):
                    if d.get(k) is not None:
                        data[k] = d[k]
                aid = d.get("agent_id") or fallback_id
                con.execute(
                    "INSERT INTO records (id, amele_id, data_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (d["id"], aid, json.dumps(data, ensure_ascii=False),
                     d.get("created_at") or "1970-01-01 00:00:00",
                     d.get("created_at") or "1970-01-01 00:00:00"))

        # 4) kaldırılan v1 tabloları
        for t in ("items", "reminders", "agents"):
            if t in _table_names(con):
                con.execute(f"DROP TABLE {t}")
                print(f"  DROPPED: {t}")

        con.commit()

        # 5) doğrulama raporu
        final = _table_names(con)
        print(f"[{path.name}] v2 tamamlandı:")
        for t in ("ameles", "records", "mcp_servers", "amele_mcp",
                  "pending_actions", "scheduled_tasks", "conversation_messages",
                  "conversation_fts"):
            print(f"  {t}: {_v1_count(con, t)}")
        lost = {"agents": n_agents, "items": n_items, "reminders": n_reminders}
        print("  taşınan:", lost, "→ hepsi yeni tablolarda mı kontrol edildi")
        print("  kalan v1 tabloları:", sorted(final & {"items", "reminders", "agents"}) or "yok")
        return 0
    except Exception as e:
        con.rollback()
        print(f"HATA — migration geri alındı (DB dokunulmadı): {e}")
        return 1
    finally:
        con.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(migrate(sys.argv[1], force="--force" in sys.argv))
