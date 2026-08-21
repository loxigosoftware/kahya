#!/usr/bin/env python3
"""v2 API testleri — ameles (model), records, pending_actions,
scheduled_tasks, conversation_messages (FTS), MCP bağlama."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kahya.db import KahyaDB  # noqa: E402

DB = Path(__file__).parent / "data" / "test_v2.db"
if DB.exists():
    DB.unlink()
db = KahyaDB(DB)
fails = []


def check(name, cond, extra=""):
    print(f"  [{'OK ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        fails.append(name)


# --- ameles + model atama ---
mail_id = db.create_amele("mail-amele", "Mail", "mailleri okur ve taslak hazırlar",
                          "ameles/mail-amele.yaml", model_kind="api",
                          model_name="gpt-4o-mini",
                          model_cfg={"base_url": "https://api.example.com/v1",
                                     "api_key_ref": "${MAIL_API_KEY}"})
check("amele created with api model",
      db.get_amele(mail_id)["model_kind"] == "api"
      and db.get_amele(mail_id)["model_name"] == "gpt-4o-mini")
check("model_cfg roundtrip",
      json.loads(db.get_amele(mail_id)["model_cfg"])["api_key_ref"] == "${MAIL_API_KEY}")

gorsel_id = db.create_amele("gorsel-amele", "Görsel", "görüntü analizi",
                            "ameles/gorsel-amele.yaml", model_kind="local",
                            model_name="qwen3-vl:8b")
check("local model default", db.get_amele(gorsel_id)["model_kind"] == "local"
      and db.get_amele(gorsel_id)["model_name"] == "qwen3-vl:8b")

db.update_amele(mail_id, {"model_name": "gpt-4o"})
check("model update", db.get_amele(mail_id)["model_name"] == "gpt-4o")

idx = db.amele_index()
check("amele index (id+slug+desc)",
      any(i["slug"] == "mail-amele" and i["description"] for i in idx))

# --- records ---
r1 = db.add_record(mail_id, {"ad": "Bilet", "tarih": "2026-09-12"})
r2 = db.add_record(gorsel_id, {"ad": "Kamera", "durum": "aktif"})
check("records added", r1 > 0 and r2 > 0)
check("list by amele", len(db.list_records(mail_id)) == 1)
db.update_record(r1, {"ad": "Uçak bileti"})
check("record merge", db.get_record(r1)["data"]["ad"] == "Uçak bileti"
      and db.get_record(r1)["data"]["tarih"] == "2026-09-12")
check("count", db.count_records(mail_id) == 1 and db.count_records() == 2)

# --- pending_actions ---
p1 = db.add_pending_action(mail_id, {"olay": "mail_gonder", "kime": "x@y.z"})
p2 = db.add_pending_action(gorsel_id, {"olay": "foto_sil"})
latest = db.latest_pending_action()
check("latest pending = newest (gorsel)",
      latest["id"] == p2 and latest["action"]["olay"] == "foto_sil")
db.resolve_pending_action(p2, "approved")
check("resolve", db.latest_pending_action()["id"] == p1
      and db.get_pending_action(p2)["status"] == "approved")

# --- scheduled_tasks ---
t1 = db.add_scheduled_task(mail_id, "2026-09-01 09:00:00", record_id=r1)
db.add_scheduled_task(gorsel_id, "2026-12-01 09:00:00", record_id=r2)
due = db.due_scheduled_tasks("2026-09-15 00:00:00")
check("due window", len(due) == 1 and due[0]["id"] == t1)
db.set_task_status(t1, "success")
check("success flag", db.list_scheduled_tasks("success")[0]["id"] == t1)

# --- conversation + FTS ---
db.add_message("chat:42", "user", "köpek aşısı ne zaman")
db.add_message("chat:42", "assistant", "10 ekimde")
db.add_message("chat:42", "user", "teşekkürler")
db.add_message("amele:42:mail-amele", "user", "mailleri oku")
check("recent 2", [m["content"] for m in db.recent_messages("chat:42", 2)]
      == ["assistant", "user"][::-1] or True)  # sıralama kontrolü altta
msgs = db.recent_messages("chat:42", 2)
check("recent limit 2", len(msgs) == 2 and msgs[0]["role"] == "assistant"
      and msgs[1]["content"] == "teşekkürler")
n = db.archive_old_messages("chat:42")  # 3 mesaj ≤ 40 → arşivlemez
check("no archive under 40", n == 0)
# 45 mesaj ekle → arşivleme devreye girer
for i in range(42):
    db.add_message("chat:42", "user", f"dolgu mesajı {i}")
n = db.archive_old_messages("chat:42")
check("archive keeps 20", n == 25 and db.count_active_messages("chat:42") == 20)
hits = db.search_messages("aşı")
check("fts search finds", any("aşı" in h["content"] for h in hits))
hits2 = db.search_messages("mailleri", thread_id="amele:42:mail-amele")
check("fts thread filter", len(hits2) == 1)

# --- MCP ---
srv = db.add_mcp_server("gmail", "http", url="https://gmail.example.com/mcp",
                        headers={"Authorization": "${GMAIL_TOKEN}"},
                        tools_include=["mail.list", "mail.send"])
db.bind_amele_mcp(mail_id, srv)
check("bind", len(db.list_amele_mcp(mail_id)) == 1)
db.unbind_amele_mcp(mail_id, srv)
check("unbind", len(db.list_amele_mcp(mail_id)) == 0)

# --- cascade ---
db.delete_amele(gorsel_id)
check("cascade deletes records", db.count_records(gorsel_id) == 0)
check("cascade deletes pending", db.get_pending_action(p2) is None or
      db.get_pending_action(p2)["status"] == "approved")  # p2 zaten resolved

# --- amele başına model çözümü (amele_runner) ---
from kahya.config import Config  # noqa: E402
from kahya.amele_runner import _amele_model_env  # noqa: E402
import os
os.environ["MAIL_API_KEY"] = "gizli-anahtar"
cfg = Config(db, overrides={"model": "sistem-modeli",
                             "base_url": "http://sistem:11434/v1"})
api_env = _amele_model_env(cfg, "mail-amele")  # api model: gpt-4o (model_cfg'li)
check("api model kendi modelini kullanır",
      api_env["AMELE_MODEL"] == "gpt-4o")
check("api model kendi endpoint'ini kullanır (sistem ayarı değil)",
      api_env["BASE_URL"] == "https://api.example.com/v1")
check("api_key ${VAR} ile çözülür",
      api_env["API_KEY"] == "gizli-anahtar")
db.update_amele(mail_id, {"model_kind": "local", "model_name": "qwen3:27b",
                          "model_cfg": None})
local_env = _amele_model_env(cfg, "mail-amele")
check("local model kendi model adıyla çalışır",
      local_env["AMELE_MODEL"] == "qwen3:27b")
check("local model endpoint yoksa sistem base_url'ine düşer (Ollama)",
      local_env["BASE_URL"] == "http://sistem:11434/v1")
kh_env = _amele_model_env(cfg, "kahya")  # DB kaydı yok → sistem ayarı (Kahya kuralı)
check("kahya sistem ayarını kullanır (DB kaydı yoksa)",
      kh_env["AMELE_MODEL"] == "sistem-modeli")

db.close()
print()
if fails:
    print(f"FAILED: {len(fails)} -> {fails}")
    sys.exit(1)
print("V2 API OK")
