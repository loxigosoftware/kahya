"""SQLite layer — schema v2 , CRUD and scheduler queries.

One file, zero setup: `data/kahya.db`. Backup = copy the file.

v2: ameles / records / mcp_servers / amele_mcp / pending_actions /
    scheduled_tasks / conversation_messages + FTS.
v1 API (items / reminders) alttaki DEPRECATED bölümünde records
üzerinden uyumluluk katmanı olarak çalışır — Step 4/6/7'de kaldırılacak.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
-- ameles (v2; v1 tablosunun yerine)
CREATE TABLE IF NOT EXISTS ameles (
  id          INTEGER PRIMARY KEY,
  slug        TEXT UNIQUE NOT NULL,      -- ^[a-z0-9_-]{1,32}$
  name        TEXT NOT NULL,             -- görünen ad
  description TEXT NOT NULL DEFAULT '',  -- amelenin ne yaptığı (Kahya bunu okur)
  schema_json TEXT,                      -- OPSİYONEL şema (alan tanımları)
  yaml_path   TEXT NOT NULL DEFAULT '',  -- ameles/<slug>.yaml (amele config)
  model_kind  TEXT NOT NULL DEFAULT 'local', -- local | api  (her amele kendi modelini seçer)
  model_name  TEXT NOT NULL DEFAULT 'qwen3:27b', -- model adı (önerilen default)
  model_cfg   TEXT,                      -- model ayarları JSON (endpoint, api_key_ref, sıcaklık...)
  enabled     INTEGER NOT NULL DEFAULT 1,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- kayıtlar (eski "items"ın yerine — her amele kendi şeklinde saklar)
CREATE TABLE IF NOT EXISTS records (
  id          INTEGER PRIMARY KEY,
  amele_id    INTEGER NOT NULL REFERENCES ameles(id) ON DELETE CASCADE,
  data_json   TEXT NOT NULL,             -- kaydın kendisi, serbest JSON
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_records_amele ON records(amele_id);

-- MCP sunucuları (Smithery'den veya elle eklenir)
CREATE TABLE IF NOT EXISTS mcp_servers (
  id            INTEGER PRIMARY KEY,
  name          TEXT UNIQUE NOT NULL,    -- ^[a-z0-9_-]{1,32}$
  kind          TEXT NOT NULL,           -- stdio | http
  command       TEXT,                    -- stdio: argv (JSON dizisi)
  url           TEXT,                    -- http: endpoint
  headers       TEXT,                    -- JSON, ${VAR} referanslı
  env           TEXT,                    -- stdio env allowlist (JSON)
  auth          TEXT,                    -- oauth ayarları (JSON) veya null
  tools_include TEXT,                    -- glob listesi (JSON)
  tools_exclude TEXT,                    -- glob listesi (JSON)
  required      INTEGER NOT NULL DEFAULT 1,
  enabled       INTEGER NOT NULL DEFAULT 1,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- amele ↔ MCP sunucu (çoktan çoğa — panelden bağlanır)
CREATE TABLE IF NOT EXISTS amele_mcp (
  amele_id  INTEGER NOT NULL REFERENCES ameles(id) ON DELETE CASCADE,
  server_id INTEGER NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
  PRIMARY KEY (amele_id, server_id)
);

-- onay kuyruğu (dile göre evet/hayır/iptal akışı)
CREATE TABLE IF NOT EXISTS pending_actions (
  id          INTEGER PRIMARY KEY,
  amele_id    INTEGER NOT NULL REFERENCES ameles(id) ON DELETE CASCADE,
  action_json TEXT NOT NULL,             -- amelenin yapmak istediği aksiyon
  status      TEXT NOT NULL DEFAULT 'waiting', -- waiting | approved | cancelled | done
  lang        TEXT NOT NULL DEFAULT 'tr', -- sorunun sorulduğu dil
  asked_at    TEXT NOT NULL DEFAULT (datetime('now')),
  resolved_at TEXT
);

-- zamanlanmış görev ("alarm" yeteneği — isteyen amele kullanır)
CREATE TABLE IF NOT EXISTS scheduled_tasks (
  id          INTEGER PRIMARY KEY,
  amele_id    INTEGER NOT NULL REFERENCES ameles(id) ON DELETE CASCADE,
  record_id   INTEGER REFERENCES records(id) ON DELETE CASCADE, -- null = sabit tarife
  run_at      TEXT NOT NULL,             -- tetikleme zamanı
  status      TEXT NOT NULL DEFAULT 'pending', -- pending | success | failed | cancelled
  attempts    INTEGER NOT NULL DEFAULT 0,
  last_error  TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- konuşma belleği (Telegram bağlamı)
-- thread_id: "chat:<chat_id>" (genel sohbet) veya "amele:<chat_id>:<slug>" (amele oturumu)
CREATE TABLE IF NOT EXISTS conversation_messages (
  id          INTEGER PRIMARY KEY,
  thread_id   TEXT NOT NULL,
  role        TEXT NOT NULL,             -- user | assistant | system | summary
  content     TEXT NOT NULL,
  archived    INTEGER NOT NULL DEFAULT 0, -- 1 = bağlamdan düştü, arşivde
  ts          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_conv_thread ON conversation_messages(thread_id, id);
CREATE INDEX IF NOT EXISTS idx_conv_archived ON conversation_messages(thread_id, archived);

-- mevcut tablolar korunur: chat_state, logs, settings, sessions, login_attempts
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

# FTS5 arşiv araması (yoksa search_messages LIKE fallback kullanır)
FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS conversation_fts USING fts5(
  content, thread_id UNINDEXED
);
"""


class KahyaDB:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")  # ON DELETE CASCADE için şart
        self.con.executescript(SCHEMA)
        try:
            self.con.executescript(FTS_SQL)
            self.fts5 = True
        except sqlite3.OperationalError:
            self.fts5 = False
        self._upgrade_schema()
        self.con.commit()

    def _upgrade_schema(self) -> None:
        """Eski DB'lerde eksik sütunları tamamlar (idempotent)."""
        cols = {r["name"] for r in self.con.execute(
            "PRAGMA table_info(scheduled_tasks)").fetchall()}
        if "attempts" not in cols:
            self.con.execute(
                "ALTER TABLE scheduled_tasks ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
        if "last_error" not in cols:
            self.con.execute(
                "ALTER TABLE scheduled_tasks ADD COLUMN last_error TEXT")

    def close(self) -> None:
        self.con.close()

    # =====================================================================
    # v2 — ameles
    # =====================================================================

    def list_ameles(self) -> list[dict]:
        rows = self.con.execute("SELECT * FROM ameles ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def get_amele(self, amele_id: int) -> Optional[dict]:
        row = self.con.execute("SELECT * FROM ameles WHERE id = ?", (amele_id,)).fetchone()
        return dict(row) if row else None

    def get_amele_by_slug(self, slug: str) -> Optional[dict]:
        row = self.con.execute("SELECT * FROM ameles WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else None

    def create_amele(self, slug: str, name: str, description: str = "",
                     yaml_path: str = "", model_kind: str = "local",
                     model_name: str = "qwen3:27b", model_cfg: Optional[dict] = None,
                     schema_json: Optional[dict] = None, enabled: int = 1) -> int:
        cur = self.con.execute(
            "INSERT INTO ameles (slug, name, description, yaml_path, model_kind,"
            " model_name, model_cfg, schema_json, enabled) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (slug, name, description, yaml_path, model_kind, model_name,
             json.dumps(model_cfg, ensure_ascii=False) if model_cfg else None,
             json.dumps(schema_json, ensure_ascii=False) if schema_json else None,
             enabled),
        )
        self.con.commit()
        return cur.lastrowid

    def update_amele(self, amele_id: int, data: dict) -> None:
        allowed = ("slug", "name", "description", "schema_json", "yaml_path",
                   "model_kind", "model_name", "model_cfg", "enabled")
        cols = [k for k in data if k in allowed]
        if not cols:
            return
        sets = []
        args: list[Any] = []
        for c in cols:
            if c == "schema_json" and data[c] is not None and not isinstance(data[c], str):
                sets.append("schema_json = ?")
                args.append(json.dumps(data[c], ensure_ascii=False))
            elif c == "model_cfg" and data[c] is not None and not isinstance(data[c], str):
                sets.append("model_cfg = ?")
                args.append(json.dumps(data[c], ensure_ascii=False))
            else:
                sets.append(f"{c} = ?")
                args.append(data[c])
        args.append(amele_id)
        self.con.execute(f"UPDATE ameles SET {', '.join(sets)} WHERE id = ?", args)
        self.con.commit()

    def delete_amele(self, amele_id: int) -> None:
        self.con.execute("DELETE FROM ameles WHERE id = ?", (amele_id,))
        self.con.commit()

    def amele_index(self) -> list[dict]:
        """Kompakt index — Kahya'nın sistem promptu için ."""
        rows = self.con.execute(
            "SELECT id, slug, description FROM ameles WHERE enabled = 1 "
            "ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    # =====================================================================
    # v2 — records (kayıtlar)
    # =====================================================================

    def add_record(self, amele_id: int, data: dict) -> int:
        cur = self.con.execute(
            "INSERT INTO records (amele_id, data_json) VALUES (?, ?)",
            (amele_id, json.dumps(data, ensure_ascii=False)),
        )
        self.con.commit()
        record_id = cur.lastrowid
        self.sync_virtual_task(amele_id, record_id, data)  # şemadaki virtual alanlar
        return record_id

    def get_record(self, record_id: int) -> Optional[dict]:
        row = self.con.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
        if not row:
            return None
        r = dict(row)
        r["data"] = json.loads(r.pop("data_json") or "{}")
        return r

    def list_records(self, amele_id: Optional[int] = None) -> list[dict]:
        q = "SELECT * FROM records"
        args: list[Any] = []
        if amele_id is not None:
            q += " WHERE amele_id = ?"
            args.append(amele_id)
        q += " ORDER BY id DESC"
        rows = self.con.execute(q, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["data"] = json.loads(d.pop("data_json") or "{}")
            out.append(d)
        return out

    def update_record(self, record_id: int, data: dict) -> None:
        """data alanlarını mevcut JSON ile birleştirir (merge)."""
        row = self.con.execute(
            "SELECT data_json FROM records WHERE id = ?", (record_id,)).fetchone()
        if not row:
            return
        merged = {**json.loads(row["data_json"] or "{}"), **data}
        self.con.execute(
            "UPDATE records SET data_json = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(merged, ensure_ascii=False), record_id),
        )
        self.con.commit()
        amele_id = self.con.execute(
            "SELECT amele_id FROM records WHERE id = ?", (record_id,)).fetchone()
        if amele_id:
            self.sync_virtual_task(amele_id["amele_id"], record_id, merged)

    def delete_record(self, record_id: int) -> None:
        self.con.execute("DELETE FROM records WHERE id = ?", (record_id,))
        self.con.commit()

    def count_records(self, amele_id: Optional[int] = None,
                      status: Optional[str] = None) -> int:
        q = "SELECT COUNT(*) AS n FROM records"
        args: list[Any] = []
        conds = []
        if amele_id is not None:
            conds.append("amele_id = ?")
            args.append(amele_id)
        if status:
            conds.append("json_extract(data_json, '$.status') = ?")
            args.append(status)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        return self.con.execute(q, args).fetchone()["n"]

    # =====================================================================
    # v2 — pending_actions (onay kuyruğu)
    # =====================================================================

    def add_pending_action(self, amele_id: int, action_json: dict,
                           lang: str = "tr") -> int:
        cur = self.con.execute(
            "INSERT INTO pending_actions (amele_id, action_json, lang) VALUES (?, ?, ?)",
            (amele_id, json.dumps(action_json, ensure_ascii=False), lang),
        )
        self.con.commit()
        return cur.lastrowid

    def latest_pending_action(self) -> Optional[dict]:
        """En güncel bekleyen onay (Step 6 kararı: cevap bununla eşleşir)."""
        row = self.con.execute(
            "SELECT * FROM pending_actions WHERE status = 'waiting' "
            "ORDER BY asked_at DESC, id DESC LIMIT 1").fetchone()
        if not row:
            return None
        r = dict(row)
        r["action"] = json.loads(r.pop("action_json") or "{}")
        return r

    def get_pending_action(self, action_id: int) -> Optional[dict]:
        row = self.con.execute(
            "SELECT * FROM pending_actions WHERE id = ?", (action_id,)).fetchone()
        if not row:
            return None
        r = dict(row)
        r["action"] = json.loads(r.pop("action_json") or "{}")
        return r

    def list_pending_actions(self, status: Optional[str] = "waiting") -> list[dict]:
        q = "SELECT * FROM pending_actions"
        args: list[Any] = []
        if status:
            q += " WHERE status = ?"
            args.append(status)
        q += " ORDER BY asked_at DESC, id DESC"
        rows = self.con.execute(q, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["action"] = json.loads(d.pop("action_json") or "{}")
            out.append(d)
        return out

    def resolve_pending_action(self, action_id: int, status: str) -> None:
        """status: approved | cancelled | done"""
        self.con.execute(
            "UPDATE pending_actions SET status = ?, resolved_at = datetime('now') "
            "WHERE id = ?", (status, action_id))
        self.con.commit()

    # =====================================================================
    # v2 — scheduled_tasks (zamanlanmış görev)
    # =====================================================================

    def add_scheduled_task(self, amele_id: int, run_at: str,
                           record_id: Optional[int] = None) -> int:
        cur = self.con.execute(
            "INSERT INTO scheduled_tasks (amele_id, record_id, run_at) VALUES (?, ?, ?)",
            (amele_id, record_id, run_at),
        )
        self.con.commit()
        return cur.lastrowid

    def bump_task_attempt(self, task_id: int, error: str) -> int:
        """Deneme sayacını artır; hata kaydını düş. Yeni attempts döner."""
        self.con.execute(
            "UPDATE scheduled_tasks SET attempts = attempts + 1, "
            "last_error = ? WHERE id = ?", (error[:400], task_id))
        self.con.commit()
        return self.con.execute(
            "SELECT attempts FROM scheduled_tasks WHERE id = ?",
            (task_id,)).fetchone()["attempts"]

    def sync_virtual_task(self, amele_id: int, record_id: int, data: dict) -> None:
        """Şemadaki virtual zaman alanlarından görev üretir .

        Örnek şema: {"fields": [{"name": "due_date", "type": "date",
        "virtual": true, "display": true}]} → data'daki due_date, kaydın
        zamanlanmış tetikleme zamanı olur (bekleyen görev varsa güncellenir).
        """
        amele = self.get_amele(amele_id)
        if not amele or not amele.get("schema_json"):
            return
        raw = amele["schema_json"]
        try:
            schema = json.loads(raw) if isinstance(raw, str) else raw
            schema = schema or {}
        except (TypeError, json.JSONDecodeError):
            return
        fields = schema.get("fields") or []
        for f in fields:
            if not isinstance(f, dict) or not f.get("virtual"):
                continue
            name = f.get("name")
            value = data.get(name)
            if not name or not value:
                continue
            v = str(value).strip()
            if len(v) == 10:  # YYYY-MM-DD → gün başlangıcı + sabit saat
                run_at = f"{v} 09:00:00"
            elif len(v) == 16:  # YYYY-MM-DD HH:MM
                run_at = f"{v}:00"
            else:
                run_at = v
            row = self.con.execute(
                "SELECT id FROM scheduled_tasks WHERE amele_id = ? AND record_id = ? "
                "AND status = 'pending'", (amele_id, record_id)).fetchone()
            if row:
                self.con.execute(
                    "UPDATE scheduled_tasks SET run_at = ? WHERE id = ?",
                    (run_at, row["id"]))
            else:
                self.con.execute(
                    "INSERT INTO scheduled_tasks (amele_id, record_id, run_at) "
                    "VALUES (?, ?, ?)", (amele_id, record_id, run_at))
        self.con.commit()

    def due_scheduled_tasks(self, now: Optional[str] = None) -> list[dict]:
        now = now or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = self.con.execute(
            "SELECT * FROM scheduled_tasks WHERE status = 'pending' AND run_at <= ? "
            "ORDER BY run_at", (now,)).fetchall()
        return [dict(r) for r in rows]

    def set_task_status(self, task_id: int, status: str) -> None:
        """status: success | failed | cancelled"""
        self.con.execute(
            "UPDATE scheduled_tasks SET status = ? WHERE id = ?", (status, task_id))
        self.con.commit()

    def list_scheduled_tasks(self, status: Optional[str] = None) -> list[dict]:
        q = "SELECT * FROM scheduled_tasks"
        args: list[Any] = []
        if status:
            q += " WHERE status = ?"
            args.append(status)
        q += " ORDER BY run_at"
        return [dict(r) for r in self.con.execute(q, args).fetchall()]

    # =====================================================================
    # v2 — conversation_messages (konuşma belleği)
    # =====================================================================

    def add_message(self, thread_id: str, role: str, content: str) -> None:
        self.con.execute(
            "INSERT INTO conversation_messages (thread_id, role, content) VALUES (?, ?, ?)",
            (thread_id, role, content))
        if self.fts5:
            try:
                self.con.execute(
                    "INSERT INTO conversation_fts (content, thread_id) VALUES (?, ?)",
                    (content, thread_id))
            except sqlite3.OperationalError:
                pass  # arama yalnız LIKE fallback'e düşer
        self.con.commit()

    def recent_messages(self, thread_id: str, limit: int = 20) -> list[dict]:
        rows = self.con.execute(
            "SELECT * FROM conversation_messages WHERE thread_id = ? AND archived = 0 "
            "ORDER BY id DESC LIMIT ?", (thread_id, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def count_active_messages(self, thread_id: str) -> int:
        return self.con.execute(
            "SELECT COUNT(*) AS n FROM conversation_messages "
            "WHERE thread_id = ? AND archived = 0", (thread_id,)).fetchone()["n"]

    def archive_old_messages(self, thread_id: str, keep: int = 20,
                             archive_from: int = 40) -> int:
        """Thread archive_from mesajı aşınca en eski (n - keep) mesajı arşivler.

        Archival every 40 messages; context = last 20 raw messages.
        """
        n = self.count_active_messages(thread_id)
        if n <= archive_from:
            return 0
        to_archive = n - keep
        ids = self.con.execute(
            "SELECT id FROM conversation_messages WHERE thread_id = ? AND archived = 0 "
            "ORDER BY id LIMIT ?", (thread_id, to_archive)).fetchall()
        ids = [r["id"] for r in ids]
        if not ids:
            return 0
        self.con.execute(
            f"UPDATE conversation_messages SET archived = 1 WHERE id IN ({','.join('?' * len(ids))})",
            ids)
        self.con.commit()
        return len(ids)

    def search_messages(self, query: str, thread_id: Optional[str] = None,
                        limit: int = 20) -> list[dict]:
        """Arşivde tam metin arama — FTS5, yoksa LIKE fallback.

        FTS5 tam token eşleşmesi ister; Türkçe çekim ekleri ("aşı" → "aşısı")
        nedeniyle sonuç boş gelirse LIKE fallback'e düşülür.
        """
        if self.fts5:
            try:
                q = "SELECT * FROM conversation_fts WHERE conversation_fts MATCH ?"
                args: list[Any] = [query]
                if thread_id:
                    q += " AND thread_id = ?"
                    args.append(thread_id)
                q += " LIMIT ?"
                rows = self.con.execute(q, args + [limit]).fetchall()
                out = []
                for r in rows:
                    m = self.con.execute(
                        "SELECT * FROM conversation_messages WHERE content = ? "
                        "ORDER BY id DESC LIMIT 1", (r["content"],)).fetchone()
                    if m:
                        out.append(dict(m))
                if out:
                    return out
                # FTS sonuçsuz → LIKE fallback (Türkçe çekimli aramalar)
            except sqlite3.OperationalError:
                pass  # FTS sorgusu geçersizse LIKE'a düş
        q = "SELECT * FROM conversation_messages WHERE content LIKE ?"
        args = [f"%{query}%"]
        if thread_id:
            q += " AND thread_id = ?"
            args.append(thread_id)
        q += " ORDER BY id DESC LIMIT ?"
        rows = self.con.execute(q, args + [limit]).fetchall()
        return [dict(r) for r in rows]

    # =====================================================================
    # v2 — MCP sunucuları
    # =====================================================================

    def add_mcp_server(self, name: str, kind: str, command: Optional[str] = None,
                       url: Optional[str] = None, headers: Optional[dict] = None,
                       env: Optional[list] = None, auth: Optional[dict] = None,
                       tools_include: Optional[list] = None,
                       tools_exclude: Optional[list] = None,
                       required: int = 1) -> int:
        cur = self.con.execute(
            "INSERT INTO mcp_servers (name, kind, command, url, headers, env, auth,"
            " tools_include, tools_exclude, required) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, kind, command, url,
             json.dumps(headers, ensure_ascii=False) if headers else None,
             json.dumps(env, ensure_ascii=False) if env else None,
             json.dumps(auth, ensure_ascii=False) if auth else None,
             json.dumps(tools_include, ensure_ascii=False) if tools_include else None,
             json.dumps(tools_exclude, ensure_ascii=False) if tools_exclude else None,
             required))
        self.con.commit()
        return cur.lastrowid

    def list_mcp_servers(self) -> list[dict]:
        rows = self.con.execute("SELECT * FROM mcp_servers ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def get_mcp_server(self, server_id: int) -> Optional[dict]:
        row = self.con.execute("SELECT * FROM mcp_servers WHERE id = ?", (server_id,)).fetchone()
        return dict(row) if row else None

    def get_mcp_server_by_name(self, name: str) -> Optional[dict]:
        row = self.con.execute("SELECT * FROM mcp_servers WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None

    def delete_mcp_server(self, server_id: int) -> None:
        self.con.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
        self.con.commit()

    def bind_amele_mcp(self, amele_id: int, server_id: int) -> None:
        self.con.execute(
            "INSERT OR IGNORE INTO amele_mcp (amele_id, server_id) VALUES (?, ?)",
            (amele_id, server_id))
        self.con.commit()

    def unbind_amele_mcp(self, amele_id: int, server_id: int) -> None:
        self.con.execute(
            "DELETE FROM amele_mcp WHERE amele_id = ? AND server_id = ?",
            (amele_id, server_id))
        self.con.commit()

    def list_amele_mcp(self, amele_id: int) -> list[dict]:
        rows = self.con.execute(
            "SELECT s.* FROM amele_mcp a JOIN mcp_servers s ON s.id = a.server_id "
            "WHERE a.amele_id = ? ORDER BY s.name", (amele_id,)).fetchall()
        return [dict(r) for r in rows]

    # =====================================================================
    # DEPRECATED v1 uyumluluk — items / reminders
    # (kaldırılacak; şimdilik records üzerinden çalışır)
    # =====================================================================

    @staticmethod
    def normalize_date(value) -> Optional[str]:
        """Coerce a due_date to YYYY-MM-DD, or None when unparseable."""
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

    @staticmethod
    def _amount_to_str(amount, currency) -> str:
        """v1 amount+currency → v2 'tutar' (örn. '3000 TRY')."""
        if amount in (None, ""):
            return ""
        s = f"{amount}"
        if currency:
            s += f" {currency}"
        return s

    @staticmethod
    def _parse_amount(tutar: str) -> tuple[Optional[float], Optional[str]]:
        """v2 'tutar' → v1 amount+currency (örn. '3000 TRY' → 3000, 'TRY')."""
        if not tutar:
            return None, None
        m = re.match(r"^\s*([\d.,]+)\s*([A-Za-z$€£₺]*)\s*$", str(tutar))
        if not m:
            return None, None
        num, cur = m.groups()
        try:
            amount = float(num.replace(",", ""))
        except ValueError:
            amount = None
        return amount, (cur or None)

    def _record_to_item(self, rec: dict) -> dict:
        """records satırını v1 items biçimine çevirir."""
        data = json.loads(rec["data_json"] or "{}")
        amount, currency = self._parse_amount(data.get("tutar", ""))
        item = {
            "id": rec["id"],
            "amele_id": rec["amele_id"],
            "title": data.get("ad", ""),
            "kind": data.get("tür", "task"),
            "amount": amount if amount is not None else data.get("amount"),
            "currency": currency or data.get("currency"),
            "due_date": data.get("due_date"),
            "repeat_rule": data.get("repeat_rule", "none"),
            "repeat_detail": data.get("repeat_detail"),
            "remind_before_days": data.get("remind_before_days", 2),
            "note": data.get("not") or data.get("note"),
            "status": data.get("status", "open"),
            "meta_json": data.get("meta_json"),
            "created_at": rec["created_at"],
            "amele_slug": None,
            "amele_name": None,
        }
        return item

    @staticmethod
    def _item_to_data(data: dict) -> dict:
        """v1 items alanlarını v2 data_json biçimine çevirir .

        v1 zorunlu sütunları (status, repeat_rule, remind_before_days) her
        zaman yazılır — JSON sorguları default değerlere güvenir.
        """
        out = {
            "ad": data.get("title"),
            "tür": data.get("kind", "task"),
            "tutar": KahyaDB._amount_to_str(data.get("amount"), data.get("currency")),
            "due_date": data.get("due_date"),
            "not": data.get("note"),
            "repeat_rule": data.get("repeat_rule", "none"),
            "remind_before_days": data.get("remind_before_days", 2),
            "status": data.get("status", "open"),
        }
        for k in ("repeat_detail", "meta_json", "amount", "currency", "note"):
            if data.get(k) is not None:
                out[k] = data[k]
        return out

    def _fallback_amele_id(self) -> int:
        """Find/create the 'v1' amele for v1 records without an amele_id."""
        row = self.con.execute("SELECT id FROM ameles WHERE slug = 'v1'").fetchone()
        if row:
            return row["id"]
        return self.create_amele("v1", "v1 (taşınan kayıtlar)",
                                 description="records carried over from v1 without an amele",
                                 yaml_path="", enabled=1)

    def insert_item(self, data: dict, amele_id: Optional[int] = None) -> int:
        if amele_id is not None:
            data = {**data, "amele_id": amele_id}
        if data.get("due_date"):
            data = {**data, "due_date": self.normalize_date(data["due_date"])}
        if data.get("amount") in ("", None):
            data = {**data, "amount": None}
        rec = self._item_to_data(data)
        aid = data.get("amele_id")
        if aid is None:
            aid = self._fallback_amele_id()
        return self.add_record(aid, rec)

    def get_item(self, item_id: int) -> Optional[dict]:
        rec = self.con.execute(
            "SELECT r.*, a.slug AS amele_slug, a.name AS amele_name "
            "FROM records r LEFT JOIN ameles a ON a.id = r.amele_id "
            "WHERE r.id = ?", (item_id,)).fetchone()
        if not rec:
            return None
        item = self._record_to_item(dict(rec))
        item["amele_slug"] = rec["amele_slug"]
        item["amele_name"] = rec["amele_name"]
        return item

    def update_item(self, item_id: int, data: dict) -> None:
        rec = self.con.execute(
            "SELECT data_json FROM records WHERE id = ?", (item_id,)).fetchone()
        if not rec:
            return
        merged = json.loads(rec["data_json"] or "{}")
        if data.get("due_date"):
            data = {**data, "due_date": self.normalize_date(data["due_date"])}
        patch = self._item_to_data(data)
        for k, v in patch.items():
            if v is not None:
                merged[k] = v
        self.con.execute(
            "UPDATE records SET data_json = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(merged, ensure_ascii=False), item_id))
        self.con.commit()

    def list_items(self, amele_slug: Optional[str] = None,
                   status: Optional[str] = None) -> list[dict]:
        q = ("SELECT r.*, a.slug AS amele_slug, a.name AS amele_name "
             "FROM records r LEFT JOIN ameles a ON a.id = r.amele_id WHERE 1=1")
        args: list[Any] = []
        if amele_slug:
            q += " AND a.slug = ?"
            args.append(amele_slug)
        if status:
            q += " AND json_extract(r.data_json, '$.status') = ?"
            args.append(status)
        q += " ORDER BY json_extract(r.data_json, '$.due_date'), r.id"
        rows = self.con.execute(q, args).fetchall()
        out = []
        for rec in rows:
            item = self._record_to_item(dict(rec))
            item["amele_slug"] = rec["amele_slug"]
            item["amele_name"] = rec["amele_name"]
            out.append(item)
        return out

    # --- reminders / scheduler (v1 uyumluluk — logs üzerinden) ---

    def mark_reminded(self, item_id: int, due_date: str) -> bool:
        """v1 reminders tablosu yerine logs'a kaydeder (UNIQUE yok — çağıran
        önce reminders_sent_today ile kontrol eder)."""
        if self.reminders_sent_today(item_id):
            return False
        self.log("scheduler", {"event": "reminder_sent", "item_id": item_id,
                               "due_date": due_date})
        return True

    def reminders_sent_today(self, item_id: int) -> bool:
        row = self.con.execute(
            "SELECT 1 FROM logs WHERE source = 'scheduler' AND ts >= date('now') "
            "AND json_extract(payload, '$.event') = 'reminder_sent' "
            "AND json_extract(payload, '$.item_id') = ? LIMIT 1", (item_id,)).fetchone()
        return row is not None

    def due_for_reminder(self, today: date) -> list[dict]:
        rows = self.con.execute(
            """SELECT r.*, a.slug AS amele_slug, a.yaml_path
               FROM records r LEFT JOIN ameles a ON a.id = r.amele_id
               WHERE json_extract(r.data_json, '$.status') = 'open'
                 AND json_extract(r.data_json, '$.due_date') IS NOT NULL
                 AND ( (date(json_extract(r.data_json, '$.due_date')) >= date(?)
                        AND date(json_extract(r.data_json, '$.due_date')) <= date(?,
                          '+' || json_extract(r.data_json, '$.remind_before_days') || ' days'))
                       OR date(json_extract(r.data_json, '$.due_date')) < date(?) )
               ORDER BY json_extract(r.data_json, '$.due_date')""",
            (today.isoformat(), today.isoformat(), today.isoformat()),
        ).fetchall()
        out = []
        for rec in rows:
            item = self._record_to_item(dict(rec))
            item["amele_slug"] = rec["amele_slug"]
            item["yaml_path"] = rec["yaml_path"]
            out.append(item)
        return out

    def next_due_date(self, item: dict) -> Optional[str]:
        """Compute the next occurrence after the current due_date."""
        rule = item.get("repeat_rule")
        detail = item.get("repeat_detail")
        if not item.get("due_date"):
            return None
        cur = date.fromisoformat(item["due_date"])
        if rule == "daily":
            return (cur + timedelta(days=1)).isoformat()
        if rule == "weekly":
            target = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                      "friday": 4, "saturday": 5, "sunday": 6}.get(
                (detail or "").lower())
            if target is None:
                return (cur + timedelta(days=7)).isoformat()
            days_ahead = (target - cur.weekday()) % 7
            days_ahead = days_ahead or 7
            return (cur + timedelta(days=days_ahead)).isoformat()
        if rule == "monthly":
            day = int(detail) if detail and detail.isdigit() else cur.day
            month = cur.month + 1
            year = cur.year + (month - 1) // 12
            month = (month - 1) % 12 + 1
            import calendar
            day = min(day, calendar.monthrange(year, month)[1])
            return date(year, month, day).isoformat()
        if rule == "yearly":
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
            return {**item, "due_date": nxt, "rolled": True}
        self.update_item(item_id, {"status": "done"})
        return {**item, "status": "done", "rolled": False}

    # =====================================================================
    # chat state (bot conversation)
    # =====================================================================

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

    # =====================================================================
    # settings (panel-editable, env as fallback)
    # =====================================================================

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

    # =====================================================================
    # sessions / login attempts
    # =====================================================================

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
        self.con.execute(
            "DELETE FROM login_attempts WHERE id NOT IN "
            "(SELECT id FROM login_attempts ORDER BY id DESC LIMIT 500)")
        self.con.commit()

    # =====================================================================
    # logs
    # =====================================================================

    def log(self, source: str, payload: dict) -> None:
        self.con.execute(
            "INSERT INTO logs (source, payload) VALUES (?, ?)",
            (source, json.dumps(payload, ensure_ascii=False)),
        )
        self.con.commit()
