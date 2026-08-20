#!/usr/bin/env python3
"""Orkestratör (Kahya) testleri — REDESIGN §3.

get_amele_profile, find_ameles, call_amele (mock LLM ile), paslama
limiti (3), search_history.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = Path(__file__).parent / "data" / "test_orch.db"
if DB.exists():
    DB.unlink()
os.environ["KAHYA_DB"] = str(DB)
os.environ["KAHYA_DIR"] = str(ROOT)

sys.path.insert(0, str(ROOT))
from kahya.db import KahyaDB  # noqa: E402

db = KahyaDB(DB)
fails = []

# --- mock LLM (amele binary için) ---
mock = subprocess.Popen(
    [sys.executable, str(Path(__file__).resolve().parent / "mock_llm.py")],
    env={**os.environ, "MOCK_PORT": "9441"})
time.sleep(1)
os.environ["AMELE_MODEL"] = "qwen3-vl:8b"
os.environ["PROVIDER_TYPE"] = "openai"
os.environ["BASE_URL"] = "http://127.0.0.1:9441/v1"
os.environ["API_KEY"] = ""
os.environ["KAHYA_LANGUAGE_NAME"] = "Turkish"


def check(name, cond, extra=""):
    print(f"  [{'OK ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        fails.append(name)


def tool(script, payload, extra_env=None):
    env = {**os.environ, "KAHYA_DB": str(DB)}
    if extra_env:
        env.update(extra_env)
    r = subprocess.run([sys.executable, str(ROOT / "tools" / script)],
                       input=json.dumps(payload), capture_output=True,
                       text=True, env=env)
    return r.returncode, r.stdout.strip()


# --- fixture: ameles ---
mail_id = db.create_amele("mail-amele", "Mail", "mailleri okur, taslak hazırlar",
                          "ameles/mail-amele.yaml", model_kind="api",
                          model_name="gpt-4o-mini",
                          model_cfg={"base_url": "http://127.0.0.1:9441/v1"})
db.create_amele("reminder-amele", "Reminder", "sets up timed reminders",
                "ameles/reminder-amele.yaml")
db.create_amele("pets-amele", "Pets", "evcil hayvan takibi",
                "ameles/pets-amele.yaml")

# --- get_amele_profile ---
rc, out = tool("get_amele_profile.py", {"amele_id": mail_id})
prof = json.loads(out) if rc == 0 else {}
check("profile by id", rc == 0 and prof["slug"] == "mail-amele"
      and prof["model_name"] == "gpt-4o-mini")
rc, out = tool("get_amele_profile.py", {"slug": "pets-amele"})
check("profile by slug", rc == 0 and json.loads(out)["name"] == "Pets")
rc, out = tool("get_amele_profile.py", {"amele_id": 999})
check("profile not found", rc == 1 and "bulunamadı" in out)

# --- find_ameles ---
rc, out = tool("find_ameles.py", {"q": "mail"})
hits = json.loads(out) if rc == 0 else []
check("find by keyword", rc == 0 and any(h["slug"] == "mail-amele" for h in hits))
rc, out = tool("find_ameles.py", {"q": "kronikleşmiş gibisinden"})
check("find no match", rc == 0 and json.loads(out) == [])

# --- call_amele (mock LLM: hedef ameleyi çalıştırır) ---
rc, out = tool("call_amele.py", {"slug": "mail-amele",
                                 "görev": "bilet rezervasyonunu özetle",
                                 "bağlam": {"mail": "uçuş 12 Eylül"}})
print(f"  call_amele out: {out[:200]}")
res = json.loads(out) if rc == 0 else {}
check("call_amele runs target", rc == 0 and res.get("slug") == "mail-amele")
check("call_amele output present", "çıktı" in res)

# --- paslama limiti (3) ---
rc, out = tool("call_amele.py", {"slug": "pets-amele", "görev": "x"},
               extra_env={"KAHYA_PASLAMA_DEPTH": "3"})
check("paslama limiti aşılınca durur", rc == 1 and "paslama limiti" in out)

# --- search_history (FTS) ---
db.add_message("chat:42", "user", "when was the dog vaccine done")
db.add_message("chat:42", "assistant", "done on 19 august")
rc, out = tool("search_history.py", {"q": "vaccine"})
hits = json.loads(out) if rc == 0 else []
check("search_history finds", rc == 0 and any("vaccine" in h["content"] for h in hits))
rc, out = tool("search_history.py", {"q": "no such thing"})
check("search_history empty", rc == 0 and json.loads(out) == [])

db.close()
mock.terminate()
print()
if fails:
    print(f"FAILED: {len(fails)} -> {fails}")
    sys.exit(1)
print("ORCHESTRATOR OK")
