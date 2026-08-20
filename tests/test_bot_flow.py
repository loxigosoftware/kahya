#!/usr/bin/env python3
"""Bot flow tests (v2, REDESIGN §4) with mock LLM + mock Telegram.

Scenarios:
  1. /amele → amele list
  2. /mail-amele selam → direct message to that amele (Kahya skipped)
  3. /mail-amele → chat mode; next messages go to the amele; /iptal exits
  4. plain message → Kahya orchestrator (with compact amele index)
  5. approval: pending action + "evet" → forwarded to its amele, resolved
  6. unknown command → redirect
  7. /help → command list
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)

TEST_ROOT = "/tmp/kahya_bot_root"
shutil.rmtree(TEST_ROOT, ignore_errors=True)
Path(TEST_ROOT).mkdir(parents=True)
ameleler_dir = Path(TEST_ROOT) / "ameleler"
ameleler_dir.mkdir()
for name in ("extract-amele.yaml", "kahya.yaml", "hatirlatıcı-amele.yaml",
             "fatura-amele.yaml", "pets-amele.yaml", "mail-amele.yaml"):
    (ameleler_dir / name).symlink_to(Path(ROOT) / "ameleler" / name)
(Path(TEST_ROOT) / "lang").symlink_to(Path(ROOT) / "lang")

# ---------------- mock LLM (fixed JSON on 9431) ----------------
mock_llm = subprocess.Popen(
    [sys.executable, str(Path(__file__).resolve().parent / "mock_llm.py")],
    env={**os.environ, "MOCK_PORT": "9431"})
time.sleep(1)

# ---------------- mock telegram ----------------
SENT = []


class TGH(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode()
        data = urllib.parse.parse_qs(body)
        if self.path.endswith("/getUpdates"):
            payload = json.dumps({"ok": True, "result": []}).encode()
        else:  # sendMessage / setMyCommands
            SENT.append({k: v[0] for k, v in data.items()})
            payload = json.dumps({"ok": True, "result": {"message_id": 1}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


tg_srv = ThreadingHTTPServer(("127.0.0.1", 9432), TGH)
threading.Thread(target=tg_srv.serve_forever, daemon=True).start()

TEST_DB = "/tmp/kahya_bot_test.db"
if os.path.exists(TEST_DB):
    os.unlink(TEST_DB)
os.environ.update({
    "KAHYA_DIR": TEST_ROOT,
    "KAHYA_DB": TEST_DB,
    "AMELE_BIN": ROOT + "/bin/amele",
    "TELEGRAM_API_BASE": "http://127.0.0.1:9432",
    "TELEGRAM_BOT_TOKEN": "test",
    "TELEGRAM_CHAT_ID": "42",
    "KAHYA_LANGUAGE": "tr",
    "AMELE_MODEL": "qwen3-vl:8b",
    "PROVIDER_TYPE": "openai",
    "BASE_URL": "http://127.0.0.1:9431/v1",
    "API_KEY": "",
})

from kahya.bot import Bot  # noqa: E402
from kahya.config import Config  # noqa: E402
from kahya.db import KahyaDB  # noqa: E402

db = KahyaDB(Path(TEST_DB))
db.create_amele("fatura-amele", "Fatura", "faturaları takip eder",
                "ameleler/fatura-amele.yaml")
mail_id = db.create_amele("mail-amele", "Mail", "mailleri okur, taslak hazırlar",
                          "ameleler/mail-amele.yaml")
db.create_amele("pets-amele", "Pets", "evcil hayvan takibi",
                "ameleler/pets-amele.yaml")
bot = Bot(Config(db), db)
fails = []


def check(name, cond, extra=""):
    print(f"  [{'OK ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        fails.append(name)


def last_msg():
    return SENT[-1]["text"] if SENT else ""


def send(text, clear=True):
    if clear:
        SENT.clear()
    bot._handle_text(42, text, db.get_chat_state(42))


# --- 1. /amele list
send("/amele")
check("1 amele list", "Ameleler" in last_msg() and "mail-amele" in last_msg()
      and "kayıt" in last_msg())

# --- 2. direct amele: /mail-amele selam
send("/mail-amele selam")
check("2 direct amele answered", "intent" in last_msg(), last_msg()[:80])
check("2 no session started", "sohbet modu" not in last_msg())

# --- 3. chat mode: /mail-amele → messages go to the amele → /iptal
send("/mail-amele")
check("3a session starts", "sohbet modu" in last_msg())
send("mailleri oku")
check("3b session message to amele", "intent" in last_msg(), last_msg()[:60])
send("/iptal")
check("3c session exits", "iptal edildi" in last_msg().lower() or "İptal" in last_msg())
check("3d state cleared", db.get_chat_state(42) == {})

# --- 4. plain message → Kahya (orchestrator, with index)
send("Bu ay hangi faturalar var?")
check("4 kahya answered", "intent" in last_msg(), last_msg()[:80])

# --- 5. approval matching: pending action + "evet"
pa_id = db.add_pending_action(mail_id, {"olay": "mail_gonder",
                                        "kime": "x@y.z"}, lang="tr")
send("evet")
pa = db.get_pending_action(pa_id)
check("5a approval forwarded", "iletildi" in last_msg(), last_msg()[:80])
check("5b action resolved approved", pa and pa["status"] == "approved")

# --- 6. unknown command
send("/bilinmeyen-komut")
check("6 unknown command", "Bilinmeyen komut" in last_msg())

# --- 7. help
send("/help")
check("7 help lists commands", "/amele" in last_msg() and "/iptal" in last_msg())

# --- 8. "iptal" cancels a pending action
pa2 = db.add_pending_action(mail_id, {"olay": "taslak_gonder"}, lang="tr")
send("iptal")
check("8a iptal resolves cancelled", db.get_pending_action(pa2)["status"] == "cancelled")
check("8b iptal message", "İptal edildi" in last_msg() or "iptal" in last_msg().lower())

# --- 9. conversation memory: mesajlar thread'e kaydediliyor
n_chat = db.con.execute(
    "SELECT COUNT(*) FROM conversation_messages WHERE thread_id = 'chat:42'"
).fetchone()[0]
check("9 chat thread kayıtları var", n_chat >= 12, f"({n_chat})")

# oturum thread'i ayrı: /mail-amele → sohbet → mesaj
send("/mail-amele")
send("bu bir oturum mesajı")
n_sess = db.con.execute(
    "SELECT COUNT(*) FROM conversation_messages WHERE thread_id = 'amele:42:mail-amele'"
).fetchone()[0]
check("9b oturum ayrı thread", n_sess >= 4, f"({n_sess})")
send("/iptal")

tg_srv.shutdown()
mock_llm.terminate()
print()
if fails:
    print("FAILED:", fails)
    sys.exit(1)
print("BOT FLOW OK")
