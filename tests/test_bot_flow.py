#!/usr/bin/env python3
"""Bot flow test with mock LLM + mock Telegram.

Flow: "3000 TL su faturasi geldi" → extract (mock LLM) → confirmation card
(no DB write yet) → "evet" → item saved.
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)

# ---------------- mock telegram ----------------
import urllib.parse

SENT = []
UPDATES = iter([
    [{"update_id": 1, "message": {"chat": {"id": 42}, "text": "3000 TL su faturasi geldi"}}],
    [{"update_id": 2, "message": {"chat": {"id": 42}, "text": "evet"}}],
    [],
])


class TGH(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode()
        data = urllib.parse.parse_qs(body)
        if self.path.endswith("/getUpdates"):
            try:
                batch = next(UPDATES)
            except StopIteration:
                batch = []
            payload = json.dumps({"ok": True, "result": batch}).encode()
        else:  # sendMessage
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
    "KAHYA_DIR": ROOT,
    "KAHYA_DB": TEST_DB,
    "AMELE_BIN": ROOT + "/bin/amele",
    "TELEGRAM_API_BASE": "http://127.0.0.1:9432",
    "TELEGRAM_BOT_TOKEN": "test",
    "TELEGRAM_CHAT_ID": "42",
    "AMELE_MODEL": "qwen3-vl:8b",
    "PROVIDER_TYPE": "openai",
    "BASE_URL": "http://127.0.0.1:9431/v1",
    "API_KEY": "",
})

from kahya.bot import Bot  # noqa: E402
from kahya.config import Config  # noqa: E402
from kahya.db import KahyaDB  # noqa: E402

cfg = Config()
db = KahyaDB(cfg.db_path)
db.create_agent("fatura", "Fatura Takipçisi", "faturalari takip et", "agents/fatura.yaml")
bot = Bot(cfg, db)
fails = []


def check(name, cond, extra=""):
    print(f"  [{'OK ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        fails.append(name)


# --- turn 1: natural language -> confirmation card, nothing saved yet
bot.run_forever = lambda: None  # we drive manually
bot._handle_text(42, "3000 TL su faturasi geldi", {})
state = db.get_chat_state(42)
check("state = confirm", state.get("step") == "confirm", str(state))
check("no item saved yet", len(db.list_items()) == 0)
confirm_msg = SENT[-1]["text"] if SENT else ""
check("confirmation card sent", "kaydedeyim mi" in confirm_msg.lower() and "su faturasi" in confirm_msg.lower())

# --- turn 2: "evet" -> saved
bot._handle_text(42, "evet", state)
items = db.list_items()
check("item saved after confirm", len(items) == 1)
check("amount saved", items[0]["amount"] == 3000.0)
check("agent linked", items[0]["agent_slug"] == "fatura")
check("repeat saved", items[0]["repeat_rule"] == "monthly")
check("state cleared", db.get_chat_state(42) == {})
saved_msg = " ".join(m["text"] for m in SENT[-2:])
check("saved confirmation sent", "Kaydedildi" in saved_msg)

# --- turn 3: 'ödedim' rolls the monthly item forward
bot._handle_text(42, "ödedim", {})
items = db.list_items()
check("paid -> rolled to next month", items and items[0]["due_date"] == "2026-09-20" and items[0]["status"] == "open")

tg_srv.shutdown()
print()
if fails:
    print("FAILED:", fails)
    sys.exit(1)
print("BOT FLOW OK")
