#!/usr/bin/env python3
"""Step 6 — onay aracı, zamanlanmış görev tarayıcısı, virtual alan testleri.

- ask_confirm tool'u: pending_actions kaydı + Telegram mesajı başlıkta
  amele adı (REDESIGN §7); Telegram ulaşmazsa kayıt iptal
- scheduler: vadesi gelen görev → amele çağrılır, success işaretlenir;
  hata → 3 deneme → failed + kullanıcıya bildirim (REDESIGN §8)
- sync_virtual_task: şemadaki virtual zaman alanı → scheduled_tasks
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)

TEST_ROOT = "/tmp/kahya_approval_root"
shutil.rmtree(TEST_ROOT, ignore_errors=True)
Path(TEST_ROOT).mkdir(parents=True)
ameleler_dir = Path(TEST_ROOT) / "ameleler"
ameleler_dir.mkdir()
for name in ("kahya.yaml", "hatirlatıcı-amele.yaml", "mail-amele.yaml",
             "fatura-amele.yaml"):
    (ameleler_dir / name).symlink_to(Path(ROOT) / "ameleler" / name)
(Path(TEST_ROOT) / "lang").symlink_to(Path(ROOT) / "lang")

# ---------------- mock LLM (fixed JSON on 9445) ----------------
mock_llm = subprocess.Popen(
    [sys.executable, str(Path(__file__).resolve().parent / "mock_llm.py")],
    env={**os.environ, "MOCK_PORT": "9445"})
time.sleep(1)

# ---------------- mock telegram (yakalanan mesajlar) ----------------
SENT = []


class TGH(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode()
        data = urllib.parse.parse_qs(body)
        SENT.append({k: v[0] for k, v in data.items()})
        payload = json.dumps({"ok": True, "result": {"message_id": 1}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


tg_srv = ThreadingHTTPServer(("127.0.0.1", 9442), TGH)
threading.Thread(target=tg_srv.serve_forever, daemon=True).start()

TEST_DB = "/tmp/kahya_approval_root/data/kahya.db"  # ask_confirm tool'u da bu yolu kullanır
if os.path.exists(TEST_DB):
    os.unlink(TEST_DB)
os.environ.update({
    "KAHYA_DIR": TEST_ROOT,
    "KAHYA_DB": TEST_DB,
    "AMELE_BIN": ROOT + "/bin/amele",
    "TELEGRAM_API_BASE": "http://127.0.0.1:9442",
    "TELEGRAM_BOT_TOKEN": "test",
    "TELEGRAM_CHAT_ID": "42",
    "KAHYA_LANGUAGE": "tr",
    "AMELE_MODEL": "qwen3-vl:8b",
    "PROVIDER_TYPE": "openai",
    "BASE_URL": "http://127.0.0.1:9445/v1",  # scheduler testinde kullanılmaz
    "API_KEY": "",
})

from kahya.config import Config  # noqa: E402
from kahya.db import KahyaDB  # noqa: E402

db = KahyaDB(Path(TEST_DB))
mail_id = db.create_amele("mail-amele", "Mail", "mailleri okur",
                          "ameleler/mail-amele.yaml")
fatura_id = db.create_amele("fatura-amele", "Fatura", "faturaları takip eder",
                            "ameleler/fatura-amele.yaml")
fails = []


def check(name, cond, extra=""):
    print(f"  [{'OK ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        fails.append(name)


# --- 1. ask_confirm tool'u ----------------------------------------------
env = {**os.environ, "KAHYA_AMELE_ID": str(mail_id)}
r = subprocess.run(
    [sys.executable, str(Path(ROOT) / "tools" / "ask_confirm.py")],
    input=json.dumps({"soru": "3 maile cevap göndereceğim", "aksiyon": {
        "olay": "mail_gonder", "adet": 3}}, ensure_ascii=False),
    capture_output=True, text=True, env=env, cwd=ROOT, timeout=60)
print("  [debug] rc:", r.returncode, "| out:", r.stdout[:150],
      "| err:", r.stderr[:150], flush=True)
out = json.loads(r.stdout.strip())
check("1a ask_confirm onay_id döner", isinstance(out.get("onay_id"), int), out)
pa = db.get_pending_action(out["onay_id"])
check("1b pending kayıt düştü", pa and pa["status"] == "waiting")
check("1c aksiyon JSON saklandı", pa and pa["action"].get("adet") == 3)
ask_msg = SENT[-1]["text"] if SENT else ""
check("1d başlıkta amele adı var", "📋" in ask_msg and "<b>mail-amele:</b>" in ask_msg,
      ask_msg[:90])
check("1e evet/hayır/iptal yönergesi var",
      "evet" in ask_msg and "hayır" in ask_msg and "iptal" in ask_msg)
db.resolve_pending_action(out["onay_id"], "cancelled")

# Telegram kapalıysa → kayıt iptal, hata döner
tg_srv.shutdown()
tg_srv.server_close()
r2 = subprocess.run(
    [sys.executable, str(Path(ROOT) / "tools" / "ask_confirm.py")],
    input=json.dumps({"soru": "test", "aksiyon": {}}),
    capture_output=True, text=True, env=env, cwd=ROOT)
check("1f telegram yoksa ERROR", r2.stdout.startswith("ERROR:"), r2.stdout[:60])
leftover = db.con.execute("SELECT COUNT(*) FROM pending_actions "
                          "WHERE status = 'waiting'").fetchone()[0]
check("1g askıda onay kalmadı", leftover == 0)

# --- 2. scheduler: due task → success --------------------------------
from kahya.scheduler import tick  # noqa: E402

run_at = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
tid = db.add_scheduled_task(mail_id, run_at, record_id=None)
tg_srv2 = ThreadingHTTPServer(("127.0.0.1", 9443), TGH)
threading.Thread(target=tg_srv2.serve_forever, daemon=True).start()
os.environ["TELEGRAM_API_BASE"] = "http://127.0.0.1:9443"
cfg = Config(db)
res = tick(cfg, db, now=datetime.now())
hit = next((x for x in res if x.get("task_id") == tid), None)
check("2a due task işlendi", hit is not None and hit.get("sent"), hit)
check("2b success işaretlendi",
      db.con.execute("SELECT status FROM scheduled_tasks WHERE id = ?",
                     (tid,)).fetchone()["status"] == "success")
ev = db.con.execute("SELECT payload FROM logs WHERE source = 'scheduler' "
                    "AND payload LIKE '%task_success%' ORDER BY id DESC LIMIT 1"
                    ).fetchone()
check("2c log düştü", ev is not None and f'"task_id": {tid}' in ev["payload"])

# --- 3. scheduler: hata → 3 deneme → failed + bildirim ------------------
tid2 = db.add_scheduled_task(fatura_id, run_at, record_id=None)
bad = Path(TEST_ROOT) / "ameleler" / "fatura-amele.yaml"
good_yaml = bad.read_text(encoding="utf-8")
bad.write_text("role_prompt: bozuk", encoding="utf-8")
try:
    # her tick bir deneme yapar (1 dk arayla) — 3 tick = 3 deneme
    for _ in range(3):
        res2 = tick(cfg, db, now=datetime.now())
    row = db.con.execute("SELECT * FROM scheduled_tasks WHERE id = ?",
                         (tid2,)).fetchone()
    check("3a 3 deneme sonrası failed",
          row["status"] == "failed" and row["attempts"] >= 3, dict(row))
    notif = next((m for m in SENT if "Zamanlanmış görev" in m.get("text", "")
                  or "Scheduled task" in m.get("text", "")), None)
    check("3b kullanıcıya bildirim gitti", notif is not None)
finally:
    bad.write_text(good_yaml, encoding="utf-8")

# --- 4. sync_virtual_task: şema virtual alanı → scheduled_tasks ---------
randevu_yaml = Path(TEST_ROOT) / "ameleler" / "randevu-amele.yaml"
randevu_yaml.write_text(
    "name: Randevu\nrole_prompt: randevuları takip eder\n", encoding="utf-8")
randevu_id = db.create_amele("randevu-amele", "Randevu", "randevuları takip eder",
                             "ameleler/randevu-amele.yaml", schema_json={"fields": [
                                 {"name": "due_date", "type": "date", "virtual": True,
                                  "display": True}]})
rid = db.add_record(randevu_id, {"ad": "deneme"})
db.sync_virtual_task(randevu_id, rid, {"ad": "deneme", "due_date": "2026-09-15"})
vt = db.con.execute("SELECT * FROM scheduled_tasks WHERE record_id = ? "
                    "AND amele_id = ? AND status = 'pending'",
                    (rid, randevu_id)).fetchone()
check("4a virtual alandan görev üretildi", vt is not None)
check("4b tarih gün başına ayarlandı", vt["run_at"] == "2026-09-15 09:00:00",
      vt["run_at"])
db.sync_virtual_task(randevu_id, rid, {"due_date": "2026-09-20"})
vt2 = db.con.execute("SELECT run_at FROM scheduled_tasks WHERE id = ?",
                     (vt["id"],)).fetchone()
check("4c bekleyen görev güncellendi", vt2["run_at"] == "2026-09-20 09:00:00",
      vt2["run_at"])
db.set_task_status(vt["id"], "success")
db.sync_virtual_task(randevu_id, rid, {"due_date": "2026-10-01"})
n_tasks = db.con.execute("SELECT COUNT(*) FROM scheduled_tasks WHERE record_id = ?",
                         (rid,)).fetchone()[0]
check("4d tamamlananın yerine yenisi üretildi", n_tasks == 2, f"({n_tasks})")

# --- 5. eski DB yükseltmesi: attempts/last_error sütunları -------------
old_db = Path("/tmp/kahya_old_schema.db")
if old_db.exists():
    old_db.unlink()
con = sqlite3.connect(old_db)
con.executescript("""
CREATE TABLE scheduled_tasks (
  id INTEGER PRIMARY KEY, amele_id INTEGER NOT NULL,
  record_id INTEGER, run_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL DEFAULT (datetime('now')));
""")
con.commit()
con.close()
up = KahyaDB(old_db)
cols = {r["name"] for r in up.con.execute(
    "PRAGMA table_info(scheduled_tasks)").fetchall()}
check("5 eski DB'ye attempts+last_error eklendi",
      "attempts" in cols and "last_error" in cols)
up.close()
old_db.unlink()

tg_srv2.shutdown()
print()
if fails:
    print("FAILED:", fails)
    sys.exit(1)
print("APPROVAL FLOW OK")
