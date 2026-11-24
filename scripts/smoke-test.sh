#!/usr/bin/env bash
# End-to-end check of auth, pagination, masking, cache and async export.
set -euo pipefail
BASE="${BASE_URL:-http://localhost:8000}"
A="X-API-Key: ${API_KEY:-dev-analyst-key}"
ADMIN="X-API-Key: ${ADMIN_KEY:-dev-admin-key}"
FROM=$(date -d '-7 days' +%F 2>/dev/null || date -v-7d +%F)
TO=$(date +%F)
jq_() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
pass() { echo "  PASS  $*"; }
fail() { echo "  FAIL  $*"; exit 1; }

echo "== health";   curl -sf "$BASE/health" | grep -q '"status":"ok"' && pass health || fail health
echo "== auth";     [ "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/v1/transactions?date_from=$FROM&date_to=$TO")" = 401 ] && pass "401 without key" || fail auth

echo "== keyset pagination"
P1=$(curl -sf -H "$A" "$BASE/v1/transactions?date_from=$FROM&date_to=$TO&limit=5")
C=$(echo "$P1" | jq_ 'd["next_cursor"]'); [ "$C" != None ] && pass "page 1 has next_cursor" || fail "no cursor"
P2=$(curl -sf -H "$A" "$BASE/v1/transactions?date_from=$FROM&date_to=$TO&limit=5&cursor=$C")
L1=$(echo "$P1" | jq_ 'd["data"][-1]["ts"]'); F2=$(echo "$P2" | jq_ 'd["data"][0]["ts"]')
[[ "$F2" < "$L1" || "$F2" == "$L1" ]] && pass "page 2 continues after page 1 ($F2 <= $L1)" || fail "page order"

echo "== masking"
echo "$P1" | jq_ 'd["data"][0]["card_number"]' | grep -q '^\*\*\*\* ' && pass "analyst sees masked card" || fail "mask"
curl -sf -H "$ADMIN" "$BASE/v1/transactions?date_from=$FROM&date_to=$TO&limit=1" | jq_ 'd["data"][0]["card_number"]' | grep -qv '\*' && pass "admin sees clear card" || fail "admin"

echo "== point lookup + cache"
ID=$((RANDOM % 1000 + 1)); URL="$BASE/v1/customers/$ID/transactions?limit=10"
curl -sf -H "$A" "$URL&cache=false" >/dev/null
H1=$(curl -s -D - -o /dev/null -H "$A" "$URL" | tr -d '\r' | awk -F': ' 'tolower($1)=="x-cache"{print $2}')
T2=$(curl -s -D - -o /dev/null -w 'time=%{time_total}' -H "$A" "$URL" | tr -d '\r')
echo "$T2" | grep -qi 'x-cache: HIT' && pass "first=$H1, second=HIT ($(echo "$T2" | grep -o 'time=.*'))" || fail cache

echo "== async export"
JOB=$(curl -sf -X POST -H "$A" -H 'Content-Type: application/json' "$BASE/v1/exports" \
  -d "{\"date_from\":\"$FROM\",\"date_to\":\"$TO\",\"merchant_category\":\"travel\"}" | jq_ 'd["job_id"]')
for _ in $(seq 1 60); do
  S=$(curl -sf -H "$A" "$BASE/v1/exports/$JOB"); ST=$(echo "$S" | jq_ 'd["status"]')
  [[ "$ST" == succeeded || "$ST" == failed ]] && break; sleep 2
done
[ "$ST" = succeeded ] || fail "export status=$ST"
URL=$(echo "$S" | jq_ 'd["download_url"]'); ROWS=$(echo "$S" | jq_ 'd["rows_written"]')
CODE=$(curl -s -o /tmp/export-smoke.parquet -w '%{http_code}' "$URL")
[ "$CODE" = 200 ] && pass "export $JOB: $ROWS rows, downloaded $(wc -c </tmp/export-smoke.parquet) bytes via presigned URL" || fail "download $CODE"
echo "All smoke tests passed."
