#!/usr/bin/env python3
"""Step 8 — MCP bağlama akış testleri (REDESIGN §6).

Panel API üzerinden: sorumluluk beyanı zorunluluğu → sunucu ekleme →
amele bağlama (YAML'a mcp: bloğu otomatik yazılır) → amele validate →
explain (MCP tool'larını görür) → run (MCP tool'unu GERÇEKTEN çağırır,
mock MCP CALLS'a düşer) → unbind (YAML temizlenir) → silme.
Smithery katalog araması ağ varsa denenir (bilgi amaçlı).
"""
import http.cookiejar
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)

TEST_ROOT = "/tmp/kahya_mcp_root"
shutil.rmtree(TEST_ROOT, ignore_errors=True)
Path(TEST_ROOT).mkdir(parents=True)
Path(TEST_ROOT, "ameleler").mkdir()
for name in ("extract-amele.yaml", "kahya.yaml", "hatirlatıcı-amele.yaml"):
    (Path(TEST_ROOT) / "ameleler" / name).symlink_to(Path(ROOT) / "ameleler" / name)
(Path(TEST_ROOT) / "lang").symlink_to(Path(ROOT) / "lang")
(Path(TEST_ROOT) / "web").symlink_to(Path(ROOT) / "web")
(Path(TEST_ROOT) / "tools").symlink_to(Path(ROOT) / "tools")

# mock MCP sunucusu (9472) — aynı process'te thread: CALLS paylaşılır
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mock_mcp as mm  # noqa: E402 — CALLS listesini okumak için
from http.server import ThreadingHTTPServer  # noqa: E402
mcp_srv = ThreadingHTTPServer(("127.0.0.1", 9472), mm.H)
threading.Thread(target=mcp_srv.serve_forever, daemon=True).start()
time.sleep(0.3)

# mock LLM (9471) — tool call akışı
mock_llm = subprocess.Popen(
    [sys.executable, str(Path(__file__).resolve().parent / "mock_llm_tools.py")],
    env={**os.environ, "MOCK_PORT": "9471", "MOCK_TOOL": "deneme__not_ekle"})
time.sleep(1)

os.environ.update({
    "KAHYA_DIR": TEST_ROOT,
    "KAHYA_DB": str(Path(TEST_ROOT) / "kahya.db"),
    "KAHYA_WEB_PORT": "8098",
    "AMELE_BIN": ROOT + "/bin/amele",
    "TEST_TOKEN": "test-token-123",   # YAML headers ${TEST_TOKEN}
})
os.environ.pop("SMITHERY_API_KEY", None)

from kahya.server import serve  # noqa: E402
from kahya.config import Config  # noqa: E402
from kahya.db import KahyaDB  # noqa: E402

db = KahyaDB(Path(os.environ["KAHYA_DB"]))
cfg = Config(db)
threading.Thread(target=lambda: serve(cfg, db), daemon=True).start()
time.sleep(1.5)

B = "http://127.0.0.1:8098"
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


def amele_env():
    return {**os.environ, "AMELE_MODEL": "x", "PROVIDER_TYPE": "openai",
            "BASE_URL": "http://127.0.0.1:9471/v1", "API_KEY": "x",
            "KAHYA_LANGUAGE_NAME": "English"}


# --- giriş + beyan ---
call("POST", "/api/login", {"user": "admin", "password": "kahya123"})
st, out = call("POST", "/api/v2/mcp/servers", {
    "name": "deneme", "kind": "http", "url": "http://127.0.0.1:9472/mcp",
    "headers": {"Authorization": "Bearer ${TEST_TOKEN}"},
    "tools_include": ["not_*"]})
check("1a beyan yokken sunucu eklenemez (403)",
      st == 403 and out.get("error") == "liability_not_accepted", out)
st, out = call("POST", "/api/settings", {"settings": {"mcp_liability_accepted": "1"}})
check("1b beyan kabul edildi", st == 200)

# --- sunucu ekleme ---
st, out = call("POST", "/api/v2/mcp/servers", {
    "name": "deneme", "kind": "http", "url": "http://127.0.0.1:9472/mcp",
    "headers": {"Authorization": "Bearer ${TEST_TOKEN}"},
    "tools_include": ["not_*"]})
check("2a sunucu eklendi", st == 201 and out.get("id"), out)
srv_id = out["id"]
st, out = call("POST", "/api/v2/mcp/servers", {
    "name": "deneme", "kind": "http", "url": "x"})
check("2b aynı isim tekrar eklenemez (409)", st == 409)
st, out = call("POST", "/api/v2/mcp/servers", {
    "name": "bozuk", "kind": "http"})
check("2c http urlsiz 400", st == 400)
st, out = call("POST", "/api/v2/mcp/servers", {
    "name": "bad name", "kind": "http", "url": "x"})
check("2d geçersiz isim 400", st == 400)

# --- amele + bağlama ---
st, out = call("POST", "/api/v2/ameleler", {
    "name": "Deneme", "slug": "deneme", "description": "notları yönetir"})
check("3a amele oluşturuldu", st == 201)
amele_id = out["id"]
st, out = call("POST", "/api/v2/mcp/bind", {"server_id": srv_id, "amele_id": amele_id})
check("3b bağlandı", st == 200, out)
yaml_path = Path(TEST_ROOT) / "ameleler" / "deneme.yaml"
yaml_text = yaml_path.read_text(encoding="utf-8")
check("3c YAML'a mcp bloğu yazıldı", "mcp:" in yaml_text
      and "name: deneme" in yaml_text and "type: http" in yaml_text
      and "url: http://127.0.0.1:9472/mcp" in yaml_text
      and "required: true" in yaml_text
      and "Bearer ${TEST_TOKEN}" in yaml_text
      and 'include: ["not_*"]' in yaml_text)

# --- amele validate (yeni binary + mcp bloğu) ---
proc = subprocess.run([ROOT + "/bin/amele", "validate", str(yaml_path)],
                      capture_output=True, text=True, env=amele_env(), timeout=30)
check("4a yaml valid (mcp bloğuyla)", proc.returncode == 0, proc.stderr[-200:])

# --- amele explain: MCP tool'larını görüyor mu ---
proc = subprocess.run([ROOT + "/bin/amele", "explain", str(yaml_path)],
                      capture_output=True, text=True, env=amele_env(), timeout=60)
out_text = proc.stdout + proc.stderr
check("4b explain MCP tool'larını listeliyor",
      "deneme__not_ekle" in out_text and "deneme__not_sil" in out_text,
      out_text[-300:])

# --- amele run: MCP tool'unu gerçekten çağırıyor mu ---
mm.CALLS.clear()
proc = subprocess.run([ROOT + "/bin/amele", "run", str(yaml_path),
                       "not ekle: test notu"],
                      capture_output=True, text=True, env=amele_env(), timeout=120)
out_text = proc.stdout + proc.stderr
check("5a run başarılı", proc.returncode == 0, f"exit {proc.returncode}: {out_text[-250:]}")
check("5b MCP tool'u çağrıldı (mock MCP CALLS)",
      any(c["name"] == "not_ekle" for c in mm.CALLS), mm.CALLS)
check("5c amele sonucu final mesajı içeriyor", "Not kaydedildi" in out_text,
      out_text[-200:])

# --- unbind: YAML temizlenir ---
st, out = call("POST", "/api/v2/mcp/unbind", {"server_id": srv_id, "amele_id": amele_id})
check("6a unbind", st == 200)
yaml_text2 = yaml_path.read_text(encoding="utf-8")
check("6b YAML'dan mcp bloğu kalktı", "mcp:" not in yaml_text2)
st, out = call("GET", "/api/v2/mcp")
srv = next((s for s in out["mcp_servers"] if s["id"] == srv_id), None)
check("6c sunucu listesinde amele yok", srv and srv["ameleler"] == [])

# --- silme ---
st, out = call("POST", "/api/v2/mcp/servers/delete", {"id": srv_id})
check("7 sunucu silindi", st == 200)
st, out = call("GET", "/api/v2/mcp")
check("7b liste boş", out["mcp_servers"] == [])

# --- status (oauth yok → boş) ---
st, out = call("GET", "/api/v2/mcp/status")
check("8 oauth status boş (statik header sunucu)", st == 200
      and out.get("oauth_status") == [])

# --- required: false → sunucu kapalıyken amele yine çalışır ---
st, out = call("POST", "/api/v2/mcp/servers", {
    "name": "opsiyonel", "kind": "http", "url": "http://127.0.0.1:9472/mcp",
    "required": False})
check("8b opsiyonel sunucu eklendi", st == 201)
opt_id = out["id"]
st, out = call("POST", "/api/v2/mcp/bind", {"server_id": opt_id, "amele_id": amele_id})
check("8c opsiyonel sunucu bağlandı", st == 200)
yaml_text3 = yaml_path.read_text(encoding="utf-8")
check("8d YAML'da required: false", "required: false" in yaml_text3)
# mock MCP'yi kapat — sunucu ulaşılamaz
mcp_srv.shutdown()
proc = subprocess.run([ROOT + "/bin/amele", "run", str(yaml_path),
                       "not ekle: test notu"],
                      capture_output=True, text=True, env=amele_env(), timeout=120)
out_text = proc.stdout + proc.stderr
check("8e sunucu kapalıyken run başarılı (required: false)",
      proc.returncode == 0, f"exit {proc.returncode}: {out_text[-200:]}")
# temizlik
call("POST", "/api/v2/mcp/unbind", {"server_id": opt_id, "amele_id": amele_id})
call("POST", "/api/v2/mcp/servers/delete", {"id": opt_id})

# --- smithery katalog araması (ağ varsa) ---
try:
    st, out = call("GET", "/api/v2/mcp/search?q=gmail")
    if st == 200 and "results" in out:
        hits = [r for r in out["results"] if "gmail" in r["qualified_name"].lower()]
        check("9 smithery araması sonuç veriyor", len(out["results"]) > 0
              and len(hits) >= 1, f"({len(out['results'])} sonuç)")
        # arama listesi connections içermez; deployment detaydan çekilir →
        # katalogdan ekleme akışını uçtan uca dene
        st, out = call("POST", "/api/v2/mcp/servers",
                       {"smithery_qualified_name": "gmail"})
        check("9b katalogdan ekleme deployment url çekiyor",
              st == 201 and out.get("url", "").startswith("https://"),
              out.get("error") or out.get("url"))
        if st == 201:
            srv_gmail = out["id"]
            st2, out2 = call("POST", "/api/v2/mcp/servers/delete",
                             {"id": srv_gmail})
            check("9c katalog sunucusu temizlendi", st2 == 200)
    else:
        check("9 smithery araması (ağ yok — atlandı)", True, f"HTTP {st}")
except Exception as e:
    check("9 smithery araması (ağ yok — atlandı)", True, str(e)[:80])

mock_llm.terminate()
mcp_srv.shutdown()
print()
if fails:
    print("FAILED:", fails)
    sys.exit(1)
print("MCP FLOW OK")
