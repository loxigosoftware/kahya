#!/usr/bin/env bash
# E2E test: auth + v2 web API (ameleler/records/tasks) + backup + scheduler dry-run.
set -euo pipefail

KAHYA_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$KAHYA_ROOT"

TEST_ROOT="/tmp/kahya_e2e_root"
rm -rf "$TEST_ROOT"
mkdir -p "$TEST_ROOT/ameleler"
ln -s "$KAHYA_ROOT/web" "$TEST_ROOT/web"
ln -s "$KAHYA_ROOT/tools" "$TEST_ROOT/tools"

export KAHYA_DIR="$TEST_ROOT"
export KAHYA_DB="$TEST_ROOT/kahya.db"
export KAHYA_WEB_PORT=8090

python3 -m kahya.server > /tmp/kahya_server.log 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null || true' EXIT
sleep 1.5

B="http://127.0.0.1:8090"
JAR="/tmp/kahya_cookies.txt"
rm -f "$JAR"
pass=0
fail=0
ck() {
  if [ "$2" = "true" ]; then echo "  [OK ] $1"; pass=$((pass+1)); else echo "  [FAIL] $1"; fail=$((fail+1)); fi
}

echo "== auth =="
out=$(curl -s "$B/api/v2/ameleler")
ck "API protected without login" "$(echo "$out" | grep -q unauthorized && echo true || echo false)"

out=$(curl -s -X POST "$B/api/login" -H 'Content-Type: application/json' \
  -d '{"user":"admin","password":"yanlis"}' -c "$JAR")
ck "wrong password rejected" "$(echo "$out" | grep -q bad_credentials && echo true || echo false)"

out=$(curl -s -X POST "$B/api/login" -H 'Content-Type: application/json' \
  -d '{"user":"admin","password":"kahya123"}' -c "$JAR")
ck "default login works" "$(echo "$out" | grep -q ok && echo true || echo false)"

for i in 1 2 3 4 5 6; do
  curl -s -X POST "$B/api/login" -H 'Content-Type: application/json' \
    -d '{"user":"admin","password":"yanlis"}' > /dev/null || true
done
out=$(curl -s -X POST "$B/api/login" -H 'Content-Type: application/json' \
  -d '{"user":"admin","password":"kahya123"}')
ck "brute-force lockout after 5 fails" "$(echo "$out" | grep -q locked && echo true || echo false)"

echo "== ameleler (v2) =="
out=$(curl -s -b "$JAR" "$B/api/v2/ameleler")
ck "authenticated amele list" "$(echo "$out" | grep -q 'ameleler' && echo true || echo false)"

out=$(curl -s -b "$JAR" -X POST "$B/api/v2/ameleler" -H 'Content-Type: application/json' \
  -d '{"name":"Fatura Takipcisi","slug":"fatura","description":"Faturalari takip et, 2 gun onceden hatirlat.","model_kind":"api","model_name":"gpt-4o-mini","model_cfg":{"base_url":"https://api.example.com/v1"},"schema_json":{"fields":[{"name":"ad","type":"text","display":true},{"name":"due_date","type":"date","display":true,"virtual":true}]}}')
ck "amele created (api model + schema)" "$(echo "$out" | grep -q '"id"' && echo true || echo false)"
ck "yaml file written" "$([ -f "$TEST_ROOT/ameleler/fatura.yaml" ] && echo true || echo false)"
ck "yaml valid amele config" "$(KAHYA_DIR="$KAHYA_ROOT" AMELE_MODEL=qwen3-vl:8b PROVIDER_TYPE=openai BASE_URL=http://localhost:11434/v1 API_KEY=x "$KAHYA_ROOT/bin/amele" validate "$TEST_ROOT/ameleler/fatura.yaml" >/dev/null 2>&1 && echo true || echo false)"

AMELE_ID=$(echo "$out" | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")
out=$(curl -s -b "$JAR" -X POST "$B/api/v2/ameleler/edit" -H 'Content-Type: application/json' \
  -d "{\"id\":$AMELE_ID,\"name\":\"Fatura Takipcisi v2\",\"description\":\"Yeni gorev tanimi\"}")
ck "amele edited" "$(echo "$out" | grep -q ok && echo true || echo false)"
ck "yaml updated" "$(grep -q 'Yeni gorev tanimi' "$TEST_ROOT/ameleler/fatura.yaml" && echo true || echo false)"

echo "== records (v2) =="
out=$(curl -s -b "$JAR" -X POST "$B/api/v2/records" -H 'Content-Type: application/json' \
  -d "{\"amele_id\":$AMELE_ID,\"data\":{\"ad\":\"Su faturasi\",\"due_date\":\"2026-09-20\"}}")
ck "record created" "$(echo "$out" | grep -q ok && echo true || echo false)"
REC_ID=$(echo "$out" | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")

out=$(curl -s -b "$JAR" "$B/api/v2/records?amele_id=$AMELE_ID")
ck "record listed" "$(echo "$out" | grep -q 'Su faturasi' && echo true || echo false)"

out=$(curl -s -b "$JAR" -X POST "$B/api/v2/records/edit" -H 'Content-Type: application/json' \
  -d "{\"id\":$REC_ID,\"data\":{\"ad\":\"Su faturasi v2\"}}")
ck "record edited" "$(echo "$out" | grep -q ok && echo true || echo false)"

out=$(curl -s -b "$JAR" "$B/api/v2/tasks")
ck "virtual field → scheduled task" "$(echo "$out" | grep -q '2026-09-20 09:00:00' && echo true || echo false)"

out=$(curl -s -b "$JAR" -X POST "$B/api/v2/records/delete" -H 'Content-Type: application/json' \
  -d "{\"id\":$REC_ID}")
ck "record deleted" "$(echo "$out" | grep -q ok && echo true || echo false)"

echo "== approvals / overview =="
out=$(curl -s -b "$JAR" "$B/api/v2/approvals")
ck "approvals endpoint" "$(echo "$out" | grep -q 'approvals' && echo true || echo false)"
out=$(curl -s -b "$JAR" "$B/api/v2/overview")
ck "overview counts" "$(echo "$out" | grep -q 'bekleyen_onaylar' && echo true || echo false)"

echo "== backup =="
out=$(curl -s -b "$JAR" "$B/api/backup/history")
ck "history backup json" "$(echo "$out" | grep -q 'mesajlar' && echo true || echo false)"
out=$(curl -s -b "$JAR" -X POST "$B/api/v2/ameleler/delete" -H 'Content-Type: application/json' \
  -d "{\"id\":$AMELE_ID}")
ck "amele deleted" "$(echo "$out" | grep -q ok && echo true || echo false)"
ck "yaml removed" "$([ ! -f "$TEST_ROOT/ameleler/fatura.yaml" ] && echo true || echo false)"

echo "== settings =="
out=$(curl -s -b "$JAR" "$B/api/settings")
ck "settings snapshot" "$(echo "$out" | grep -q web_port && echo true || echo false)"
out=$(curl -s -b "$JAR" -X POST "$B/api/settings" -H 'Content-Type: application/json' \
  -d '{"settings":{"language":"en","web_port":"8099"}}')
ck "settings saved" "$(echo "$out" | grep -q ok && echo true || echo false)"

echo "== scheduler dry-run (v1 uyumluluk) =="
out=$(KAHYA_DIR="$TEST_ROOT" KAHYA_DB="$TEST_ROOT/kahya.db" python3 "$KAHYA_ROOT/tests/sched_probe.py")
ck "scheduler dry-run sees window item" "$(echo "$out" | grep -q 'window_item' && echo true || echo false)"

echo
echo "PASS=$pass FAIL=$fail"
[ "$fail" -eq 0 ]
