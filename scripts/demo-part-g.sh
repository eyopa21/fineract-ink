#!/usr/bin/env bash
# Part G demo: Mock ERP behaviours against the running stack (make up).
#   - seeded chart of accounts
#   - POST /api/accounts idempotent on external_reference (same payload x4 -> one account; different payload -> 409)
#   - POST /api/journal-entries must balance (unbalanced -> 422), idempotent on external_reference
#   - GET /api/accounts/{code}/balance derived from entries
#   - fault injection: down -> 503, timeout_after_write -> entry persisted, retry is a replay
set -euo pipefail
ERP=${ERP_URL:-http://localhost:${MOCK_ERP_PORT:-8101}}
KEY=${ERP_API_KEY:-erp-demo-key}
S=$(date +%s | tail -c 6)

api() { # method path [json]
  local m=$1 p=$2 b=${3:-}
  curl -s -o /tmp/erp_body -w '%{http_code}' -H "X-API-Key: $KEY" -H "Content-Type: application/json" -H "X-Correlation-ID: demo-g-$S" \
       -X "$m" "$ERP$p" ${b:+-d "$b"}
}
show() { echo "   HTTP $1  $(head -c 400 /tmp/erp_body)"; echo; }
count_accounts() { api GET /api/accounts >/dev/null; python3 -c "import json;print(len(json.load(open('/tmp/erp_body'))))"; }
count_by_ref() { api GET /api/accounts >/dev/null; python3 -c "import json;print(sum(1 for a in json.load(open('/tmp/erp_body')) if a['external_reference']=='$1'))"; }

echo "== Part G demo against $ERP (suffix $S)"
echo
echo "== Seeded chart of accounts"
api GET /api/accounts >/dev/null; python3 -c "import json;[print('   ',a['code'],a['type'].ljust(9),a['name'],'',a['external_reference']) for a in json.load(open('/tmp/erp_body'))]"
echo "-- accounts: $(count_accounts)"

ACC='{"code":"13'$S'","name":"Demo Receivable","type":"ASSET","currency":"RWF","external_reference":"GL-DEMO-'$S'"}'
echo; echo "== 1. POST /api/accounts, new reference GL-DEMO-$S"; code=$(api POST /api/accounts "$ACC"); show "$code"
echo "== 2. Same payload 3 more times"
for i in 1 2 3; do code=$(api POST /api/accounts "$ACC"); echo "   attempt $i: HTTP $code"; done
echo "-- accounts carrying GL-DEMO-$S: $(count_by_ref GL-DEMO-$S)   (expected 1)"
echo; echo "== 3. Same reference, different name"; code=$(api POST /api/accounts "${ACC/Demo Receivable/Renamed}"); show "$code"
echo "== 4. Same code, different reference (DB uniqueness on code)"; code=$(api POST /api/accounts "${ACC/GL-DEMO-$S/GL-OTHER-$S}"); show "$code"
echo "-- accounts: $(count_accounts)"

JE='{"external_reference":"LN-DEMO-'$S'-DISB","entry_date":"2026-01-15","description":"Disbursement demo","currency":"RWF",
     "lines":[{"account_code":"1200","direction":"DEBIT","amount":"5000000.00"},{"account_code":"1100","direction":"CREDIT","amount":"5000000.00"}],
     "metadata":{"loan_reference":"LN-DEMO-'$S'","transaction_type":"DISBURSEMENT"}}'
echo; echo "== 5. Balances before"; for c in 1100 1200; do api GET /api/accounts/$c/balance >/dev/null; echo "   $c: $(cat /tmp/erp_body)"; done
echo; echo "== 6. POST /api/journal-entries, unbalanced (credit 4,000,000)"; code=$(api POST /api/journal-entries "${JE/CREDIT\",\"amount\":\"5000000.00/CREDIT\",\"amount\":\"4000000.00}"); show "$code"
echo "== 7. POST /api/journal-entries, balanced"; code=$(api POST /api/journal-entries "$JE"); show "$code"
EID=$(python3 -c "import json;print(json.load(open('/tmp/erp_body'))['entry_id'])")
echo "== 8. Same entry 3 more times"; for i in 1 2 3; do code=$(api POST /api/journal-entries "$JE"); echo "   attempt $i: HTTP $code entry_id=$(python3 -c "import json;print(json.load(open('/tmp/erp_body'))['entry_id'])")"; done
echo "== 9. Same reference, different amount"; code=$(api POST /api/journal-entries "${JE//5000000.00/4999999.00}"); show "$code"
echo "== 10. GET /api/journal-entries/LN-DEMO-$S-DISB"; code=$(api GET "/api/journal-entries/LN-DEMO-$S-DISB"); show "$code"
echo "== 11. Balances after (one entry of 5,000,000 applied once)"; for c in 1100 1200; do api GET /api/accounts/$c/balance >/dev/null; echo "   $c: $(cat /tmp/erp_body)"; done
echo "-- entries carrying LN-DEMO-$S-DISB in DB: $(docker compose exec -T postgres psql -U erp -d erp -tAc "select count(*) from journal_entries where external_reference='LN-DEMO-$S-DISB'" 2>/dev/null || echo '?')   (expected 1)"

echo; echo "== 12. Fault injection: down"; api PUT /admin/faults '{"mode":"down"}' >/dev/null; code=$(api GET /api/accounts); show "$code"
echo "== 13. Fault injection: timeout_after_write (1s), then retry after the timeout"
api PUT /admin/faults '{"mode":"timeout_after_write","delay_seconds":1}' >/dev/null
JE2="${JE//LN-DEMO-$S-DISB/LN-DEMO-$S-R1}"
code=$(api POST /api/journal-entries "$JE2"); echo "   first call: HTTP $code (took >1s, entry_id=$(python3 -c "import json;print(json.load(open('/tmp/erp_body'))['entry_id'])"))"
api PUT /admin/faults '{"mode":"none"}' >/dev/null
code=$(api POST /api/journal-entries "$JE2"); echo "   retry:      HTTP $code entry_id=$(python3 -c "import json;print(json.load(open('/tmp/erp_body'))['entry_id'])")"
echo "-- entries carrying LN-DEMO-$S-R1 in DB: $(docker compose exec -T postgres psql -U erp -d erp -tAc "select count(*) from journal_entries where external_reference='LN-DEMO-$S-R1'" 2>/dev/null)   (expected 1)"
echo; echo "== 14. Structured log sample (correlation_id demo-g-$S)"
docker compose logs --no-log-prefix mock-erp 2>/dev/null | grep "demo-g-$S" | grep -E "posted|replayed|conflict|fault" | head -5 | cut -c1-230
echo; echo "== done"
