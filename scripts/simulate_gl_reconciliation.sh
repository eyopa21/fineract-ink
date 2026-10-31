#!/usr/bin/env bash
# Stretch (Part L): GL master-data reconciliation, Fineract GL accounts vs the ERP chart of accounts.
set -euo pipefail
API=${INTEGRATION_URL:-http://localhost:${INTEGRATION_PORT:-8100}}
KEY=${API_KEY:-partner-demo-key}
echo "== GL master-data reconciliation: POST $API/api/reconciliation/gl-accounts (read-only)"
curl -s -H "X-API-Key: $KEY" -X POST "$API/api/reconciliation/gl-accounts" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('   summary:', d['summary'])
for i in d['items']:
    e = i.get('erp') or {}; f = i.get('fineract') or {}
    print('   %-16s %-20s erp=%-34s fineract=%-40s mapping=%s %s' % (i['key'], i['status'], (e.get('code'), e.get('name')) if e else '-', (f.get('code'), f.get('name'), f.get('type')) if f else '-', i.get('mapping_state'), i.get('note') or ''))
"
echo "   Repair for MISSING_IN_FINERACT is the create-only sync (POST /api/accounts/sync); MISSING_IN_ERP (accounts created outside the sync) needs a human."
