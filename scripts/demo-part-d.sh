#!/usr/bin/env bash
# Part D: one loan end to end through the Integration Service, capturing Fineract's actual status and
# balances after every stage. Also exercises Part C (client by client_uid) and Part F (replays).
#   application -> approval -> disbursement -> 3 partial repayments -> final payoff -> Closed (obligations met)
set -euo pipefail
API=${INTEGRATION_URL:-http://localhost:${INTEGRATION_PORT:-8100}}
KEY=${API_KEY:-partner-demo-key}
ERP=${ERP_URL:-http://localhost:${MOCK_ERP_PORT:-8101}}
S=$(date +%s | tail -c 7)
UID_="SME-$(printf '%06d' $((10#${S:0:6} % 1000000)))"
CID="demo-d-$S"

call() { # method path [json]
  local m=$1 p=$2 b=${3:-}
  curl -s -o /tmp/d_body -w '%{http_code}' -H "X-API-Key: $KEY" -H "Content-Type: application/json" -H "X-Correlation-ID: $CID" -X "$m" "$API$p" ${b:+-d "$b"}
}
py() { python3 -c "import json,sys; d=json.load(open('/tmp/d_body')); $1"; }
loan_state() { # prints status + balances + tx count from GET /api/loans/{ref}
  call GET "/api/loans/$1" >/dev/null
  py "s=d['status']; b=d['balances']; print('   status=%s active=%s closed_obligations_met=%s' % (s['value'], s['active'], s['closed_obligations_met'])); print('   principal_outstanding=%s interest_outstanding=%s total_outstanding=%s total_repayment=%s transactions=%d' % (b['principal_outstanding'], b['interest_outstanding'], b['total_outstanding'], b['total_repayment'], len(d['transactions'])))"
}
erp_entry() { curl -s -H "X-API-Key: erp-demo-key" "$ERP/api/journal-entries/$1" | python3 -c "import sys,json; d=json.load(sys.stdin); print('   ERP entry %s: %s' % (d.get('entry_id'), [(l['account_code'],l['direction'],l['amount']) for l in d.get('lines',[])])) if 'entry_id' in d else print('   ERP: not found')"; }

echo "== Part D demo ($CID) client_uid=$UID_"
echo
echo "== Part C: create client, find by client_uid, validate"
code=$(call POST /api/clients "{\"external_reference\":\"CLI-$S\",\"client_uid\":\"$UID_\",\"full_name\":\"Kigali Tools Ltd $S\",\"activation_date\":\"2026-01-10\"}"); echo "   POST /api/clients -> $code $(head -c 200 /tmp/d_body)"
code=$(call POST /api/clients "{\"external_reference\":\"CLI-$S\",\"client_uid\":\"$UID_\",\"full_name\":\"Kigali Tools Ltd $S\",\"activation_date\":\"2026-01-10\"}"); echo "   same request again -> $code (replay)"
code=$(call GET "/api/clients/$UID_"); py "print('   GET /api/clients/$UID_ -> $code exists=%s active=%s name_ok=%s' % (d['client_uid']=='$UID_', d['active'], d['full_name']=='Kigali Tools Ltd $S'))"

echo; echo "== 1. Loan application + approval (5,000,000 RWF, 12 months, 18%)"
code=$(call POST /api/loans "{\"external_reference\":\"APP-$S\",\"client_uid\":\"$UID_\",\"principal\":\"5000000\",\"submitted_on\":\"2026-01-12\",\"expected_disbursement_date\":\"2026-01-15\"}"); echo "   POST /api/loans -> $code $(cat /tmp/d_body)"
LOAN=$(py "print(d['loan_reference'])")
loan_state "$LOAN"

echo; echo "== 2. Disbursement (2026-01-15), amount omitted: the approved principal is disbursed"
code=$(call POST "/api/loans/$LOAN/disburse" "{\"external_reference\":\"$LOAN-DISB\",\"disbursed_on\":\"2026-01-15\"}"); py "print('   -> $code state=%s fineract_tx=%s erp_entry=%s breakdown=%s' % (d.get('state'), d.get('fineract_transaction_id'), d.get('erp_entry_id'), d.get('fineract_breakdown')))"
loan_state "$LOAN"; erp_entry "$LOAN-DISB"

n=0
for when in 2026-02-15 2026-03-15 2026-04-15; do
  n=$((n+1)); echo; echo "== $((n+2)). Repayment $n (458,333 RWF on $when)"
  code=$(call POST "/api/loans/$LOAN/repayments" "{\"external_reference\":\"$LOAN-R$n\",\"amount\":\"458333\",\"paid_on\":\"$when\"}"); py "b=d.get('fineract_breakdown') or {}; print('   -> $code state=%s fineract_tx=%s erp_entry=%s principal=%s interest=%s' % (d.get('state'), d.get('fineract_transaction_id'), d.get('erp_entry_id'), b.get('principal'), b.get('interest')))"
  loan_state "$LOAN"; erp_entry "$LOAN-R$n"
done

echo; echo "== 6. Final payoff: pay Fineract's total_outstanding (2026-05-15)"
call GET "/api/loans/$LOAN" >/dev/null; DUE=$(py "print(int(float(d['balances']['total_outstanding'])))")
echo "   total_outstanding per Fineract = $DUE"
code=$(call POST "/api/loans/$LOAN/repayments" "{\"external_reference\":\"$LOAN-PAYOFF\",\"amount\":\"$DUE\",\"paid_on\":\"2026-05-15\"}"); py "b=d.get('fineract_breakdown') or {}; print('   -> $code state=%s fineract_tx=%s erp_entry=%s principal=%s interest=%s' % (d.get('state'), d.get('fineract_transaction_id'), d.get('erp_entry_id'), b.get('principal'), b.get('interest')))"
loan_state "$LOAN"

echo; echo "== Transactions as recorded by Fineract (via GET /api/loans/$LOAN)"
call GET "/api/loans/$LOAN" >/dev/null; py "[print('   %-28s %-13s %-10s amount=%-9s principal=%-9s interest=%-8s outstanding_after=%s' % (t['external_reference'], t['type'], t['date'], t['amount'], t['principal'], t['interest'], t['outstanding_after'])) for t in d['transactions']]"
echo; echo "== Integration transaction records"; for r in DISB R1 R2 R3 PAYOFF; do call GET "/api/transactions/$LOAN-$r" >/dev/null; py "print('   %-28s %-10s fineract_tx=%-4s erp_entry=%s' % (d['external_reference'], d['state'], d['fineract_transaction_id'], d['erp_entry_id']))"; done
echo; echo "== ERP balances"; for c in 1100 1200 4000; do curl -s -H "X-API-Key: erp-demo-key" "$ERP/api/accounts/$c/balance" | python3 -c "import sys,json; d=json.load(sys.stdin); print('   %s %s balance=%s %s (entries=%s)' % (d['code'], d['type'], d['balance_amount'], d['balance_side'], d['entry_count']))"; done
echo; echo "-- done: loan $LOAN"
