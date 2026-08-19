#!/usr/bin/env bash
# E2E test: web API + scheduler dry-run on a throwaway DB + throwaway root.
set -euo pipefail

KAHYA_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$KAHYA_ROOT"

TEST_ROOT="/tmp/kahya_e2e_root"
rm -rf "$TEST_ROOT"
mkdir -p "$TEST_ROOT/agents"
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
pass=0
fail=0
ck() {
  if [ "$2" = "true" ]; then echo "  [OK ] $1"; pass=$((pass+1)); else echo "  [FAIL] $1"; fail=$((fail+1)); fi
}

echo "== agents =="
out=$(curl -s "$B/api/agents")
ck "empty agent list" "$(echo "$out" | grep -q 'agents' && echo true || echo false)"

out=$(curl -s -X POST "$B/api/agents" -H 'Content-Type: application/json' \
  -d '{"name":"Fatura Takipcisi","slug":"fatura","role_prompt":"Faturalari ve son odeme tarihlerini takip et, 2 gun onceden hatirlat."}')
ck "agent created" "$(echo "$out" | grep -q 'ok' && echo true || echo false)"

ck "yaml file written" "$([ -f "$TEST_ROOT/agents/fatura.yaml" ] && echo true || echo false)"
ck "yaml contains role" "$(grep -q 'Faturalari ve son odeme' "$TEST_ROOT/agents/fatura.yaml" && echo true || echo false)"
ck "yaml is valid amele config" "$(KAHYA_DIR="$KAHYA_ROOT" AMELE_MODEL=qwen3-vl:8b PROVIDER_TYPE=openai BASE_URL=http://localhost:11434/v1 API_KEY= "$KAHYA_ROOT/bin/amele" validate "$TEST_ROOT/agents/fatura.yaml" >/dev/null 2>&1 && echo true || echo false)"

out=$(curl -s -X POST "$B/api/agents" -H 'Content-Type: application/json' \
  -d '{"name":"Kotu Slug","slug":"Bad Slug!","role_prompt":"x"}')
ck "bad slug rejected" "$(echo "$out" | grep -q 'slug' && echo true || echo false)"

echo "== items =="
out=$(curl -s -X POST "$B/api/items" -H 'Content-Type: application/json' \
  -d '{"data":{"title":"Su faturasi","amount":3000,"currency":"TRY","due_date":"2026-09-20","agent":"fatura","repeat_rule":"monthly","repeat_detail":"20","remind_before_days":2}}')
ck "item created" "$(echo "$out" | grep -q 'ok' && echo true || echo false)"
ITEM_ID=$(echo "$out" | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")

out=$(curl -s "$B/api/items?status=open")
ck "item listed with agent" "$(echo "$out" | grep -q 'Fatura Takipcisi' && echo true || echo false)"

out=$(curl -s -X POST "$B/api/items/$ITEM_ID/complete" -H 'Content-Type: application/json' -d '{}')
ck "complete rolls to next month" "$(echo "$out" | grep -q '2026-10-20' && echo true || echo false)"

echo "== scheduler dry-run =="
out=$(KAHYA_DIR="$TEST_ROOT" KAHYA_DB="$TEST_ROOT/kahya.db" python3 "$KAHYA_ROOT/tests/sched_probe.py")
ck "scheduler dry-run sees window item" "$(echo "$out" | grep -q 'window_item' && echo true || echo false)"

echo
echo "PASS=$pass FAIL=$fail"
[ "$fail" -eq 0 ]
