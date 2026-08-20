#!/usr/bin/env python3
"""Bot flow tests with mock LLM + mock Telegram.

Scenarios:
  1. record: "3000 TL su faturasi geldi" → extract → confirmation card
     (no DB write yet) → "evet" → item saved
  2. "ödedim" → monthly item rolls forward
  3. /agents → agent list message
  4. /add-agent wizard: name → slug → role → confirm → YAML file written
  5. question: extract returns intent=question → orchestrator agent
     (kahya.yaml) is spawned and answers
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
             "fatura-amele.yaml", "pets-amele.yaml"):
    (ameleler_dir / name).symlink_to(Path(ROOT) / "ameleler" / name)
(Path(TEST_ROOT) / "lang").symlink_to(Path(ROOT) / "lang")

# ---------------- mock LLM (record intent on 9431) ----------------
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
db.create_agent("fatura", "Fatura Takipçisi", "faturalari takip et", "agents/fatura.yaml")
bot = Bot(Config(db), db)
fails = []


def check(name, cond, extra=""):
    print(f"  [{'OK ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        fails.append(name)


def last_msg():
    return SENT[-1]["text"] if SENT else ""


# --- 1. natural language record flow
bot._handle_text(42, "3000 TL su faturasi geldi", {})
state = db.get_chat_state(42)
check("1a state=confirm", state.get("step") == "confirm")
check("1b nothing saved yet", len(db.list_items()) == 0)
check("1c confirmation card", "kaydedeyim mi" in last_msg().lower() and "su faturasi" in last_msg().lower())

bot._handle_text(42, "evet", state)
items = db.list_items()
check("1d saved after confirm", len(items) == 1 and items[0]["amount"] == 3000.0)
check("1e agent linked", items[0]["agent_slug"] == "fatura")
check("1f state cleared", db.get_chat_state(42) == {})
check("1g saved message", "kaydedildi" in " ".join(m["text"] for m in SENT[-2:]).lower())

# --- 2. paid → monthly roll
bot._handle_text(42, "ödedim", {})
items = db.list_items()
check("2a rolled to next month", items and items[0]["due_date"] == "2026-09-20"
      and items[0]["status"] == "open")

# --- 3. /agents
bot._handle_text(42, "/agents", {})
check("3 agents listed", "fatura" in last_msg().lower() and "fatura takip" in last_msg().lower())

# --- 4. /add-agent wizard
bot._handle_text(42, "/add-agent", {})
check("4a asks name", "ajan ad" in last_msg().lower())
bot._handle_text(42, "Abonelik Takipçisi", db.get_chat_state(42))
check("4b asks slug", "slug" in last_msg().lower())
bot._handle_text(42, "subscriptions", db.get_chat_state(42))
check("4c asks role", "görev tanımı" in last_msg().lower())
bot._handle_text(42, "Abonelikleri takip et", db.get_chat_state(42))
check("4d confirm card", "onaylıyor musunuz" in last_msg().lower())
bot._handle_text(42, "evet", db.get_chat_state(42))
check("4e created message", "oluşturuldu" in last_msg().lower())
check("4f yaml written", (ameleler_dir / "subscriptions.yaml").exists())
check("4g yaml valid amele", os.system(
    f"AMELE_MODEL=qwen3-vl:8b PROVIDER_TYPE=openai BASE_URL=http://localhost:11434/v1 API_KEY= "
    f"{ROOT}/bin/amele validate {ameleler_dir}/subscriptions.yaml >/dev/null 2>&1") == 0)

# --- 5. question → orchestrator
Q = {"intent": "question", "title": "Kuduz asisi ne zamandi", "kind": "other",
     "agent_slug": "pets", "amount": None, "currency": None, "due_date": None,
     "repeat_rule": "none", "repeat_detail": None, "remind_before_days": 0,
     "note": None, "ask_user": None}
mock = subprocess.Popen(
    [sys.executable, str(Path(__file__).resolve().parent / "mock_llm.py")],
    env={**os.environ, "MOCK_JSON": json.dumps(Q), "MOCK_PORT": "9433"})
time.sleep(1)
try:
    n_before = len(SENT)
    bot._handle_text(42, "Kuduz asisi ne zamandi?", {})
    check("5 orchestrator answered", len(SENT) > n_before, last_msg()[:60])
finally:
    mock.terminate()

tg_srv.shutdown()
mock_llm.terminate()
print()
if fails:
    print("FAILED:", fails)
    sys.exit(1)
print("BOT FLOW OK")
