#!/usr/bin/env bash
# Part A demo: ERP-owned GL accounts in the customized Fineract.
# Shows behaviours 1-4 against the running stack (make up). Prints state before and after each call.
#
#   1. external reference persisted on the GL account and unique
#   2. same reference + identical payload -> existing account returned (no duplicate)
#   3. same reference + different payload -> HTTP 409
#   4. endpoint protected by Fineract's permission model (CREATE_GLACCOUNT)
set -euo pipefail

FINERACT=${FINERACT_URL:-http://localhost:${FINERACT_PORT:-8080}/fineract-provider/api/v1}
AUTH=${FINERACT_AUTH:-mifos:password}
TENANT=${FINERACT_TENANT:-default}
SUFFIX=$(date +%s | tail -c 6)
EXT_REF="GL-DEMO-$SUFFIX"
GL_CODE="DEMO$SUFFIX"

api() { # method path [json] [auth]
  local method=$1 path=$2 body=${3:-} auth=${4:-$AUTH}
  curl -s -o /tmp/demo_body -w '%{http_code}' -u "$auth" -H "Fineract-Platform-TenantId: $TENANT" -H "Content-Type: application/json" \
       -X "$method" "$FINERACT$path" ${body:+-d "$body"}
}
show() { local code=$1; echo "   HTTP $code"; sed 's/^/   /' /tmp/demo_body | head -c 700; echo; }
count_by_ref() { api GET /glaccounts >/dev/null; python3 -c "import json,sys; d=json.load(open('/tmp/demo_body')); print(sum(1 for a in d if a.get('externalId')=='$EXT_REF'))"; }
payload() { printf '{"name":"%s","glCode":"%s","type":1,"usage":1,"manualEntriesAllowed":true,"description":"ERP-owned","externalId":"%s"}' "$1" "$2" "$EXT_REF"; }

echo "== Part A demo against $FINERACT (reference $EXT_REF, code $GL_CODE)"
echo
echo "-- before: GL accounts carrying $EXT_REF: $(count_by_ref)"

echo
echo "== 1. Create with externalId (new reference)"
code=$(api POST /glaccounts "$(payload 'Loan Receivable' "$GL_CODE")"); show "$code"
ID=$(python3 -c "import json; print(json.load(open('/tmp/demo_body'))['resourceId'])")
echo "   -> created id $ID; GET shows the persisted reference:"
code=$(api GET "/glaccounts/$ID"); python3 -c "import json; d=json.load(open('/tmp/demo_body')); print('   ', {k: d[k] for k in ('id','glCode','name','externalId')})"
echo "-- after: GL accounts carrying $EXT_REF: $(count_by_ref)"

echo
echo "== 2. Same reference, identical payload, sent 3 more times"
for i in 1 2 3; do
  code=$(api POST /glaccounts "$(payload 'Loan Receivable' "$GL_CODE")")
  python3 -c "import json; d=json.load(open('/tmp/demo_body')); print('   attempt $i: HTTP $code resourceId=%s replay=%s' % (d.get('resourceId'), d.get('changes',{}).get('idempotentReplay')))"
done
echo "-- after: GL accounts carrying $EXT_REF: $(count_by_ref)   (expected 1)"

echo
echo "== 3. Same reference, different payload (name changed)"
code=$(api POST /glaccounts "$(payload 'Loan Receivable RENAMED' "$GL_CODE")"); show "$code"
echo "   (expected HTTP 409, error.msg.glaccount.externalid.conflict)"
echo "-- after: GL accounts carrying $EXT_REF: $(count_by_ref)   (expected 1)"

echo
echo "== 4. Permission model: a user without CREATE_GLACCOUNT"
code=$(api POST /roles '{"name":"gl-demo-role-'$SUFFIX'","description":"no permissions"}'); ROLE=$(python3 -c "import json; print(json.load(open('/tmp/demo_body'))['resourceId'])")
code=$(api POST /staff '{"officeId":1,"firstname":"GL","lastname":"Demo'$SUFFIX'","isLoanOfficer":false,"locale":"en","dateFormat":"dd MMMM yyyy","joiningDate":"01 January 2024"}'); STAFF=$(python3 -c "import json; print(json.load(open('/tmp/demo_body'))['resourceId'])")
USER="gldemo$SUFFIX"; PASS='A1b2c3d4e5f$'
code=$(api POST /users '{"username":"'$USER'","firstname":"GL","lastname":"Demo","email":"gl'$SUFFIX'@example.com","officeId":1,"staffId":'$STAFF',"roles":['$ROLE'],"sendPasswordToEmail":false,"password":"'$PASS'","repeatPassword":"'$PASS'"}')
echo "   created role $ROLE (no permissions) and user $USER (HTTP $code)"
code=$(api POST /glaccounts "$(payload 'Bank' "X$GL_CODE")" "$USER:$PASS"); show "$code"
echo "   (expected HTTP 403 naming CREATE_GLACCOUNT)"
echo "-- after: GL accounts carrying $EXT_REF: $(count_by_ref)   (expected 1)"

echo
echo "== Database view (unique constraint on acc_gl_account.external_id)"
docker compose exec -T postgres psql -U postgres -d fineract_default -c "select id, gl_code, name, external_id from acc_gl_account where external_id = '$EXT_REF';" 2>/dev/null || echo "   (postgres container not reachable from here)"
docker compose exec -T postgres psql -U postgres -d fineract_default -tAc "select conname from pg_constraint where conrelid='acc_gl_account'::regclass and conname like '%external%';" 2>/dev/null | sed 's/^/   constraint: /'
echo
echo "== done"
