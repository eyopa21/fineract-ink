#!/usr/bin/env bash
# Part A (Python side) demo: ERP chart of accounts -> Integration Service -> Fineract GL accounts.
#   - explicit trigger POST /api/accounts/sync
#   - per-account status SYNC_PENDING -> SYNCED | FAILED, FAILED retried on the next call
#   - same accounts sent 4 times -> one ERP account, one Fineract GL account, one mapping row each
set -euo pipefail
INTEGRATION=${INTEGRATION_URL:-http://localhost:${INTEGRATION_PORT:-8100}}
KEY=${API_KEY:-partner-demo-key}
FINERACT=${FINERACT_URL:-http://localhost:${FINERACT_PORT:-8080}/fineract-provider/api/v1}
ERP=${ERP_URL:-http://localhost:${MOCK_ERP_PORT:-8101}}
CID="demo-sync-$(date +%s | tail -c 6)"

sync() { curl -s -H "X-API-Key: $KEY" -H "X-Correlation-ID: $CID" -X POST "$INTEGRATION/api/accounts/sync"; }
summary() { python3 -c "import sys,json; d=json.load(sys.stdin); print('   requested=%d created=%d replayed=%d already_synced=%d resolved_by_lookup=%d failed=%d' % (d['requested'],d['created'],d['replayed'],d['already_synced'],d['resolved_by_lookup'],d['failed'])); [print('   %-8s %-8s %-13s fineract_id=%-4s attempts=%s %s' % (a['external_reference'],a['erp_code'],a['status'],a['fineract_gl_account_id'],a['attempts'],(a['last_error'] or '')[:70])) for a in d['accounts']]"; }
fin_count() { curl -s -u mifos:password -H "Fineract-Platform-TenantId: default" "$FINERACT/glaccounts" | python3 -c "import sys,json; d=json.load(sys.stdin); refs=[a.get('externalId') for a in d if (a.get('externalId') or '').startswith('GL-') and a.get('externalId','')[3:].isdigit()]; print(len(refs), 'ERP-synced GL accounts in Fineract:', sorted(refs))"; }
erp_count() { curl -s -H "X-API-Key: erp-demo-key" "$ERP/api/accounts" | python3 -c "import sys,json; print(len(json.load(sys.stdin)), 'accounts in ERP')"; }
map_count() { curl -s -H "X-API-Key: $KEY" "$INTEGRATION/api/accounts/sync" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d), 'mapping rows,', sum(1 for a in d if a['status']=='SYNCED'), 'SYNCED,', sum(1 for a in d if a['status']=='FAILED'), 'FAILED')"; }

echo "== GL sync demo ($CID)"
echo "-- before: $(erp_count) | $(fin_count) | $(map_count)"
echo; echo "== 1. POST /api/accounts/sync"; sync | summary
echo "-- after:  $(erp_count) | $(fin_count) | $(map_count)"
echo; echo "== 2. Same sync 3 more times"
for i in 1 2 3; do echo "   call $i:"; sync | summary | head -1; done
echo "-- after:  $(erp_count) | $(fin_count) | $(map_count)"
echo; echo "== 3. Fineract down (injected), then sync"
curl -s -H "X-API-Key: $KEY" -X PUT "$INTEGRATION/admin/faults" -H "Content-Type: application/json" -d '{"mode":"fineract_down"}' >/dev/null
sync | summary | head -1
curl -s -H "X-API-Key: $KEY" -X PUT "$INTEGRATION/admin/faults" -H "Content-Type: application/json" -d '{"mode":"none"}' >/dev/null
echo "   (SYNCED rows are never re-sent, so Fineract being down costs nothing once synced)"
echo; echo "== 4. Structured log sample"
echo "-- done"
