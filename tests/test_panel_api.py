#!/usr/bin/env python3
"""Step 7 — panel v2 HTTP API testleri.

Server'ı test root'unda başlatır; login + tüm v2 route'ları dener:
amele CRUD (model+şema), records CRUD, approvals resolve (mock LLM),
tasks, mcp, backup history. Eski v1 route'ların kaldırıldığını da doğrular.
"""
import http.cookiejar
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)

TEST_ROOT = "/tmp/kahya_panel_root"
shutil.rmtree(TEST_ROOT, ignore_errors=True)
Path(TEST_ROOT).mkdir(parents=True)
Path(TEST_ROOT, "ameleler").mkdir()
for name in ("extract-amele.yaml", "kahya.yaml", "hatirlatıcı-amele.yaml",
             "mail-amele.yaml", "fatura-amele.yaml"):
    (Path(TEST_ROOT) / "ameleler" / name).symlink_to(Path(ROOT) / "ameleler" / name)
(Path(TEST_ROOT) / "lang").symlink_to(Path(ROOT) / "lang")
(Path(TEST_ROOT) / "web").symlink_to(Path(ROOT) / "web")

# mock LLM (approvals resolve için)
mock_llm = subprocess.Popen(
    [sys.executable, str(Path(__file__).resolve().parent / "mock_llm.py")],
    env={**os.environ, "MOCK_PORT": "9461"})
time.sleep(1)

os.environ.update({
    "KAHYA_DIR": TEST_ROOT,
    "KAHYA_DB": str(Path(TEST_ROOT) / "kahya.db"),
    "KAHYA_WEB_PORT": "8095",
    "AMELE_BIN": ROOT + "/bin/amele",
    "PROVIDER_TYPE": "openai",
    "BASE_URL": "http://127.0.0.1:9461/v1",
    "API_KEY": "",
    "AMELE_MODEL": "qwen3-vl:8b",
})
os.environ.pop("SMITHERY_API_KEY", None)

from kahya.server import serve  # noqa: E402
from kahya.config import Config  # noqa: E402
from kahya.db import KahyaDB  # noqa: E402

db = KahyaDB(Path(os.environ["KAHYA_DB"]))
cfg = Config(db)
srv = None
threading.Thread(target=lambda: serve(cfg, db), daemon=True).start()
time.sleep(1.5)

B = "http://127.0.0.1:8095"
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
fails = []


def check(name, cond, extra=""):
    print(f"  [{'OK ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        fails.append(name)


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(B + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


# --- auth ---
st, out = call("GET", "/api/v2/ameleler")
check("1a API korumalı (401)", st == 401)
st, out = call("POST", "/api/login", {"user": "admin", "password": "yanlis"})
check("1b yanlış şifre reddedildi", st == 401 and out.get("error") == "bad_credentials")
st, out = call("POST", "/api/login", {"user": "admin", "password": "kahya123"})
check("1c varsayılan giriş", st == 200 and out.get("ok"))
req = urllib.request.Request(B + "/login")
with opener.open(req, timeout=30) as r:
    html = r.read().decode()
check("1d panel servis ediliyor", "<title>Kâhya" in html and "tabs_overview" in html)

# --- ameleler CRUD + model + şema ---
st, out = call("GET", "/api/v2/ameleler")
check("2a amele listesi", st == 200 and isinstance(out.get("ameleler"), list))

st, out = call("POST", "/api/v2/ameleler", {
    "name": "Fatura", "slug": "fatura-test", "description": "faturaları takip eder",
    "model_kind": "api", "model_name": "gpt-4o-mini",
    "model_cfg": {"base_url": "https://api.example.com/v1"},
    "schema_json": {"fields": [{"name": "ad", "type": "text", "display": True},
                               {"name": "due_date", "type": "date", "display": True,
                                "virtual": True}]}})
check("2b api model + şema ile oluşturma", st == 201 and out.get("id"))
aid = out["id"]
check("2c YAML yazıldı", (Path(TEST_ROOT) / "ameleler" / "fatura-test.yaml").exists())

st, out = call("GET", "/api/v2/ameleler")
a = next(x for x in out["ameleler"] if x["id"] == aid)
check("2d model + şema roundtrip", a["model_kind"] == "api"
      and a["model_cfg"]["base_url"] == "https://api.example.com/v1"
      and a["schema_json"]["fields"][1]["virtual"] is True)

st, out = call("POST", "/api/v2/ameleler", {"name": "x", "slug": "x", "description": "y"})
check("2e model_kind geçersizse 400", st == 400)
st, out = call("POST", "/api/v2/ameleler", {
    "name": "X", "slug": "api-test", "description": "z",
    "model_kind": "api", "model_name": "m", "model_cfg": None})
check("2f api model base_url'siz 400", st == 400)

st, out = call("POST", "/api/v2/ameleler/edit", {
    "id": aid, "name": "Fatura v2", "description": "yeni tanım",
    "model_kind": "local", "model_name": "qwen3:27b", "model_cfg": None,
    "schema_json": None})
check("2g düzenleme", st == 200)
yaml_text = (Path(TEST_ROOT) / "ameleler" / "fatura-test.yaml").read_text(encoding="utf-8")
check("2h YAML güncellendi", "yeni tanım" in yaml_text)
st, out = call("GET", "/api/v2/ameleler")
a = next(x for x in out["ameleler"] if x["id"] == aid)
check("2i model local'e döndü, şema silindi", a["model_kind"] == "local"
      and a["schema_json"] is None)

# --- records CRUD ---
st, out = call("POST", "/api/v2/records", {"amele_id": aid,
                                           "data": {"ad": "Su faturası", "due_date": "2026-09-20"}})
check("3a kayıt oluşturma", st == 201 and out.get("id"))
rid = out["id"]
st, out = call("GET", "/api/v2/records?amele_id=%d" % aid)
check("3b kayıt listesi + amele adı", st == 200
      and out["records"][0]["data"]["ad"] == "Su faturası"
      and out["records"][0]["amele_slug"] == "fatura-test")
st, out = call("GET", "/api/v2/records?amele_id=%d&q=su" % aid)
check("3c arama", st == 200 and len(out["records"]) == 1)
st, out = call("GET", "/api/v2/records?amele_id=%d&q=olmayan" % aid)
check("3d arama boş", st == 200 and len(out["records"]) == 0)
st, out = call("POST", "/api/v2/records/edit", {"id": rid, "data": {"ad": "Su v2"}})
check("3e kayıt düzenleme", st == 200)
st, out = call("POST", "/api/v2/records/edit", {"id": 99999, "data": {}})
check("3f olmayan kayıt 404", st == 404)
st, out = call("POST", "/api/v2/records/delete", {"id": rid})
check("3g kayıt silme", st == 200)

# --- approvals ---
from kahya.db import KahyaDB as _D  # noqa: E401
pa_id = db.add_pending_action(aid, {"olay": "mail_gonder", "kime": "x@y.z"}, lang="tr")
st, out = call("GET", "/api/v2/approvals")
check("4a bekleyen onay listesi", st == 200 and len(out["approvals"]) == 1
      and out["approvals"][0]["amele_slug"] == "fatura-test")
st, out = call("POST", "/api/v2/approvals/resolve", {"id": pa_id, "karar": "approved"})
print("  [debug] 4b:", st, out, flush=True)
check("4b onayla → amele çağrılır, resolve edilir", st == 200)
st, out = call("POST", "/api/v2/approvals/resolve", {"id": pa_id, "karar": "approved"})
check("4c çözülmüş onay tekrar çözülemez", st == 400)
st, out = call("POST", "/api/v2/approvals/resolve", {"id": 99999, "karar": "approved"})
check("4d olmayan onay 404", st == 404)
pa2 = db.add_pending_action(aid, {"olay": "foto_sil"}, lang="tr")
st, out = call("POST", "/api/v2/approvals/resolve", {"id": pa2, "karar": "cancelled"})
check("4e iptal kararı (amele çağrılmaz)", st == 200
      and db.get_pending_action(pa2)["status"] == "cancelled")

# --- tasks / overview / mcp ---
st, out = call("GET", "/api/v2/tasks?status=pending")
check("5a tasks listesi", st == 200 and isinstance(out.get("tasks"), list))
st, out = call("GET", "/api/v2/overview")
check("5b overview sayaçları", st == 200 and "bekleyen_onaylar" in out
      and "yaklasan_gorevler" in out)
st, out = call("GET", "/api/v2/mcp")
check("5c mcp listesi", st == 200 and "mcp_servers" in out)

# --- backup history ---
req = urllib.request.Request(B + "/api/backup/history")
with opener.open(req, timeout=30) as r:
    body = json.loads(r.read().decode())
check("6a geçmiş yedeği JSON", "mesajlar" in body and "exported_at" in body)
req = urllib.request.Request(B + "/api/backup")
with opener.open(req, timeout=30) as r:
    head = r.headers.get("Content-Disposition", "")
check("6b DB yedeği indir", "kahya-backup" in head)

# --- eski v1 route'lar kalktı ---
st, out = call("GET", "/api/items")
check("7a /api/items kaldırıldı (404)", st == 404)
st, out = call("GET", "/api/agents")
check("7b eski v1 agents endpoint kaldırıldı (404)", st == 404)
st, out = call("POST", "/api/items", {"data": {"title": "x"}})
check("7c item oluşturma kaldırıldı (404)", st == 404)

# --- smithery anahtarı ayarlarda ---
st, out = call("POST", "/api/settings", {"settings": {"smithery_api_key": "sk-test"}})
check("8a smithery anahtarı kaydedildi", st == 200)
st, out = call("GET", "/api/settings")
check("8b anahtar set bayrağı (düz değil)", out["settings"].get("smithery_api_key_set") is True
      and out["settings"].get("smithery_api_key") is None)

mock_llm.terminate()
print()
if fails:
    print("FAILED:", fails)
    sys.exit(1)
print("PANEL API OK")
