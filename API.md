# API

Two services expose REST APIs. The partner uses the Integration Service only; the Mock ERP API is used by
the Integration Service and by operators. Both publish OpenAPI docs at `/docs`.

| Service | Base URL (host) | Auth header | Key |
|---|---|---|---|
| Integration Service | `http://localhost:8100` | `X-API-Key` | `partner-demo-key` |
| Mock ERP | `http://localhost:8101` | `X-API-Key` | `erp-demo-key` |

Every response carries `X-Correlation-ID`, echoing the request header or a generated UUID.
Money is sent as a string with up to two decimals; RWF has no minor unit, so whole francs in practice.
Dates are ISO `YYYY-MM-DD`.

## Error format

Every failure, in both services, has the same body:

```json
{"error": {"code": "CONFLICT", "message": "loan LN-2026-000001 already has a disbursement (LN-2026-000001-DISB, COMPLETED)",
           "details": [{"field": "loan_reference", "existing_reference": "LN-2026-000001-DISB"}]},
 "correlation_id": "8a5d4c3e-..."}
```

| HTTP | code | When |
|---|---|---|
| 401 | UNAUTHORIZED | missing or wrong `X-API-Key` |
| 404 | NOT_FOUND | unknown client_uid, loan_reference, external_reference |
| 409 | CONFLICT | same reference with a different payload; client_uid reused; second disbursement on a loan; loan not pending approval |
| 422 | VALIDATION_ERROR | request validation, or Fineract validation (its messages in `details`) |
| 422 | BUSINESS_RULE | Fineract refused a state change (for example repaying a loan that is not active) |
| 502 | UPSTREAM_ERROR | any other Fineract/ERP error, upstream status in `details` |
| 503 | UPSTREAM_UNAVAILABLE | Fineract or ERP unreachable, or a Fineract lock conflict; "nothing was recorded; safe to retry" |
| 504 | UPSTREAM_TIMEOUT | Fineract or ERP timed out on a read path |
| 500 | INTERNAL_ERROR | unexpected; also the simulated crash fault |

## Idempotency contract (Part F)

Every state-changing request carries `external_reference`, unique per business operation. Uniqueness is
scoped by operation type and enforced by a unique constraint in `idempotency_keys`.

| Case | Response |
|---|---|
| new reference | processed once |
| same reference, same payload, finished | the original status and body, header `X-Idempotent-Replay: true` |
| same reference, same payload, in progress | 202 `{"status": "IN_PROGRESS", ..., "transaction": {...current state...}}`; a second execution never starts |
| same reference, different payload | 409 CONFLICT |
| same reference after a FAILED attempt (nothing happened) | processed again; a corrected payload is accepted |

Per operation:

| Operation | Scope | Extra guarantee |
|---|---|---|
| GL account sync | per `external_reference` | code unique in the ERP and in Fineract |
| client creation | per `external_reference` | `client_uid` unique at the database level; reuse under another reference is 409 |
| loan application | per `external_reference` | one `loan_reference` issued per reference |
| loan approval | per `external_reference` | loan must be pending approval, else 409 |
| disbursement | per `external_reference` and one per loan | partial unique index; concurrent different references still yield one; Fineract's loan state is the last line of defence |
| repayment | per `external_reference` | many per loan; Fineract transaction externalId unique |

## Integration Service endpoints

### GL sync

`POST /api/accounts/sync` → 200, no body required.
```json
{"requested": 9, "created": 9, "replayed": 0, "already_synced": 0, "resolved_by_lookup": 0, "failed": 0,
 "accounts": [{"external_reference": "GL-1100", "erp_code": "1100", "status": "SYNCED", "fineract_gl_account_id": 10, "attempts": 1, ...}]}
```
`GET /api/accounts/sync` → the mapping table.

### Clients (Part C)

`POST /api/clients` → 201 (200 on replay)
```json
{"external_reference": "CLI-2026-000001", "client_uid": "SME-000001", "full_name": "Kigali Tools Ltd", "activation_date": "2026-01-10"}
```
```json
{"client_uid": "SME-000001", "full_name": "Kigali Tools Ltd", "status": "ACTIVE", "active": true, "activation_date": "2026-01-10", "office": "Kigali Main Office"}
```
`GET /api/clients/{client_uid}` → same shape, 404 if unknown.

### Loans (Parts D, E)

`POST /api/loans` → 201. Approval happens in the same call unless `approve: false`.
```json
{"external_reference": "APP-2026-000001", "client_uid": "SME-000001", "principal": "5000000",
 "submitted_on": "2026-01-12", "expected_disbursement_date": "2026-01-15", "approve": true}
```
```json
{"loan_reference": "LN-2026-000001", "client_uid": "SME-000001", "principal": "5000000", "status": "APPROVED"}
```
Principal must be within the product's range, 100,000 to 50,000,000 RWF; Fineract's message is returned as 422 otherwise.

`POST /api/loans/{loan_reference}/approve` → 200
```json
{"external_reference": "APR-2026-000001", "approved_on": "2026-01-13"}
```

`GET /api/loans/{loan_reference}` → Fineract's status, balances and transactions.
```json
{"loan_reference": "LN-2026-000001", "client_uid": "SME-000001", "product": "SME Asset Finance", "principal": "5000000",
 "status": {"code": "loanStatusType.active", "value": "Active", "active": true, "closed_obligations_met": false},
 "balances": {"principal_disbursed": "5000000", "principal_paid": "383333", "principal_outstanding": "4616667",
              "interest_charged": "500799", "interest_paid": "75000", "interest_outstanding": "425799",
              "total_expected_repayment": "5500799", "total_repayment": "458333", "total_outstanding": "5042466"},
 "transactions": [{"external_reference": "LN-2026-000001-DISB", "type": "Disbursement", "date": "2026-01-15", "amount": "5000000", ...},
                  {"external_reference": "LN-2026-000001-R1", "type": "Repayment", "date": "2026-02-15", "amount": "458333",
                   "principal": "383333", "interest": "75000", "fees": "0", "penalties": "0", "outstanding_after": "4616667", "reversed": false}]}
```

`POST /api/loans/{loan_reference}/disburse` → 200 when COMPLETED, 202 while the ERP leg is pending.
```json
{"external_reference": "LN-2026-000001-DISB", "disbursed_on": "2026-01-15"}
```
`amount` is optional: the product is single-disbursement, so the approved principal is disbursed. If sent,
it must equal the approved principal (422 otherwise, before anything reaches Fineract).

`POST /api/loans/{loan_reference}/repayments` → 200 / 202
```json
{"external_reference": "LN-2026-000001-R1", "amount": "458333", "paid_on": "2026-02-15"}
```

Transaction body (disburse, repayments, `GET /api/transactions/{external_reference}`):
```json
{"external_reference": "LN-2026-000001-R1", "operation": "REPAYMENT", "loan_reference": "LN-2026-000001",
 "amount": "458333", "transaction_date": "2026-02-15", "state": "COMPLETED", "fineract_transaction_id": 3,
 "fineract_breakdown": {"amount": "458333.0", "principal": "383333.0", "interest": "75000.0", "fees": "0", "penalties": "0", "overpayment": "0"},
 "erp_entry_id": 10, "attempts": 1, "last_error": null}
```
`fineract_transaction_id` and `erp_entry_id` are the two systems' own ids, exposed for tracing only; the
public identifier is `external_reference`.

### Reconciliation (Part K)

`POST /api/reconciliation` → 200. Give at least one of `loan_references`, `external_references`, `date_from`/`date_to`.
```json
{"loan_references": ["LN-2026-000001"], "repair": false}
```
```json
{"scope": {...}, "repair": false,
 "summary": {"MATCHED": 2, "MISSING_IN_ERP": 1, "MISSING_IN_FINERACT": 1, "AMOUNT_MISMATCH": 1},
 "items": [{"external_reference": "LN-2026-000001-R2", "loan_reference": "LN-2026-000001", "status": "MISSING_IN_ERP",
            "fineract": {"type": "REPAYMENT", "amount": "150000.0", "date": "2026-03-15", "reversed": false, "fineract_transaction_id": 9},
            "erp": null, "note": null, "integration_state": "FINERACT_DONE", "repair": null}]}
```
With `"repair": true`, MISSING_IN_ERP items that this service recorded are re-posted (idempotent) and
reported as MATCHED with `"repair": {"action": "post", "outcome": "posted", "erp_entry_id": 91}`.

### GL master-data reconciliation (stretch)

`POST /api/reconciliation/gl-accounts` → 200, no body. Fineract GL accounts versus the ERP chart of
accounts, keyed by external reference. Statuses MATCHED, MISSING_IN_ERP (a Fineract GL account with no
ERP account, or an external reference the ERP does not know), MISSING_IN_FINERACT (ERP account not
synced; repair is `POST /api/accounts/sync`), ATTRIBUTE_MISMATCH (code, name or type differ). Read-only.
```json
{"summary": {"MATCHED": 9, "MISSING_IN_ERP": 1, "MISSING_IN_FINERACT": 0, "ATTRIBUTE_MISMATCH": 0},
 "items": [{"key": "GL-1200", "status": "MATCHED", "erp": {"code": "1200", "name": "Loan Receivable", "type": "ASSET"},
            "fineract": {"code": "1200", "name": "Loan Receivable", "type": "ASSET", "disabled": false}, "mapping_state": "SYNCED", "note": null}]}
```

### Operations

| Endpoint | Purpose |
|---|---|
| `GET /health`, `GET /ready` | liveness; readiness checks the database |
| `POST /admin/recover` | run the recovery sweep now (also runs at startup) |
| `GET/PUT /admin/faults` | `{"mode": "none" \| "fineract_down" \| "fineract_timeout_after_send" \| "erp_down" \| "crash_after_fineract"}` |

## Mock ERP endpoints

| Endpoint | Behaviour |
|---|---|
| `POST /api/accounts` | 201; idempotent on `external_reference` (200 replay, `X-Idempotent-Replay`), 409 on a different payload or a reused `code` |
| `GET /api/accounts` | chart of accounts |
| `GET /api/accounts/{code}/balance` | `{"debits","credits","balance","balance_side","balance_amount","entry_count"}`, computed from lines |
| `POST /api/journal-entries` | 201; must balance (422), known accounts, matching currency; idempotent on `external_reference` |
| `GET /api/journal-entries/{external_reference}` | one entry |
| `GET /api/journal-entries?loan_reference=&date_from=&date_to=` | listing used by reconciliation |
| `GET/PUT /admin/faults` | `{"mode": "none" \| "down" \| "timeout" \| "timeout_after_write", "delay_seconds": 30}` |

Account payload: `{"code": "1200", "name": "Loan Receivable", "type": "ASSET", "currency": "RWF", "external_reference": "GL-1200"}`.
Journal entry payload:
```json
{"external_reference": "LN-2026-000001-R1", "entry_date": "2026-02-15", "description": "Repayment LN-2026-000001", "currency": "RWF",
 "lines": [{"account_code": "1100", "direction": "DEBIT", "amount": "458333"},
           {"account_code": "1200", "direction": "CREDIT", "amount": "383333"},
           {"account_code": "4000", "direction": "CREDIT", "amount": "75000"}],
 "metadata": {"loan_reference": "LN-2026-000001", "transaction_type": "REPAYMENT", "fineract_transaction_id": "3", "source_reference": "LN-2026-000001-R1", "reversal": false}}
```

## Fineract customization

`POST /fineract-provider/api/v1/glaccounts` accepts `externalId`; identical replay returns the existing
account with `changes.idempotentReplay = true`; different payload → 409 `error.msg.glaccount.externalid.conflict`;
`GET /glaccounts` and `GET /glaccounts/{id}` return `externalId`. Details in `fineract-customization/README.md`.
