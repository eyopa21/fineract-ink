# Architecture

Three services, one Postgres, one command to start them. Fineract is the system of record for clients,
loans and balances; the Mock ERP is the accounting master (chart of accounts and journal); the Integration
Service is the only thing partners talk to and the only thing that talks to both.

```
 financing partner
        │  REST, X-API-Key, business identifiers only
        ▼
┌──────────────────────────┐    POST /glaccounts (externalId)        ┌────────────────────────┐
│   Integration Service    │ ───────────────────────────────────────▶│  Apache Fineract 1.15  │
│   Python / FastAPI       │    clients, loans, disburse, repay,      │  + Part A patch        │
│   db: integration        │    lookups by external id                │  db: fineract_default  │
│                          │ ◀───────────────────────────────────────│                        │
│  idempotency_keys        │    transaction breakdown (principal,     └────────────────────────┘
│  clients / loans         │    interest, fees, penalties)
│  loan_transactions       │
│  gl_account_mappings     │    GET /api/accounts, POST /api/journal-entries,
│                          │    GET /api/journal-entries?loan_reference   ┌────────────────────┐
│  recovery sweep          │ ◀──────────────────────────────────────────▶│  Mock ERP          │
└──────────────────────────┘                                              │  Python / FastAPI  │
                                                                          │  db: erp           │
                                                                          └────────────────────┘
```

All four run as containers on one Docker network; Postgres holds four databases, one per owner, each with
its own user. Nothing is shared below the HTTP layer.

## Components

| Component | Language | Role | Code |
|---|---|---|---|
| Fineract customization | Java 21 / Spring Boot | ERP external reference on GL accounts, idempotent create, 409 on conflict, Liquibase migration | `fineract-customization/gl-external-reference.patch` |
| Mock ERP | Python 3.12 / FastAPI | Chart of accounts, balanced double-entry journal, DB-level uniqueness, fault injection | `mock-erp/app` |
| Integration Service | Python 3.12 / FastAPI | Partner API, GL sync, loan lifecycle, idempotency, state machine, recovery, reconciliation, Fineract setup command | `integration-service/app` |
| Postgres 18 | | `fineract_tenants`, `fineract_default`, `erp`, `integration` | `docker/postgres/init.sh` |

No queue, no worker, no cache. Outbound calls are synchronous and every step is persisted before and after
each external call, so an explicit recovery sweep (startup and `POST /admin/recover`) can finish anything a
crash or outage interrupted. See DECISIONS.md for why.

## Data flow

### GL sync (Part A)

```
ERP chart of accounts ──GET /api/accounts──▶ Integration Service ──POST /glaccounts {externalId}──▶ Fineract
                                                    │ gl_account_mappings: SYNC_PENDING → SYNCED | FAILED
                                                    └ type map ASSET=1 LIABILITY=2 EQUITY=3 INCOME=4 EXPENSE=5, usage DETAIL
```

Trigger: `POST /api/accounts/sync` (explicit), also run by the setup command before the loan product is
created. Create-only. Fineract's patched endpoint returns the existing account on an identical replay and
409 on a different payload for the same reference, so the sync can be repeated at will.

### Loan operations (Parts C to H)

```
partner ─POST /api/loans/{ref}/repayments {external_reference, amount}─▶ Integration Service
   1. idempotency claim (unique operation_type + external_reference)         ─ 202/200/409 if seen before
   2. loan_transactions row RECEIVED → FINERACT_PENDING                       ─ partial unique index: one disbursement per loan
   3. POST /loans/external-id/{loan_reference}/transactions?command=repayment {externalId = external_reference}
   4. GET  /loans/external-id/{loan}/transactions/external-id/{ref}            ─ Fineract's breakdown, stored → FINERACT_DONE
   5. map breakdown → journal entry (pure function, Part H)                    → ERP_PENDING
   6. POST /api/journal-entries {external_reference}                          → COMPLETED (ERP idempotent on the reference)
   7. idempotency key COMPLETED with the final body
```

Fineract is addressed by external ids throughout: `client_uid` is the client externalId, `loan_reference`
the loan externalId, and every disbursement or repayment reference the transaction externalId. Numeric
Fineract ids are stored in the mapping tables and never returned.

### Reconciliation (Part K)

```
POST /api/reconciliation {loan_references | external_references | date range, repair?}
   Fineract: GET /loans/external-id/{loan}?associations=transactions   (disbursements + repayments with an externalId)
   ERP:      GET /api/journal-entries?loan_reference=&date_from=&date_to=
   compare() by external_reference → MATCHED | MISSING_IN_ERP | MISSING_IN_FINERACT | AMOUNT_MISMATCH
   repair=true → re-post MISSING_IN_ERP from the stored Fineract breakdown (idempotent); nothing else is touched
```

## Data model (Integration Service, Alembic 0001 to 0003)

| Table | Key columns | Constraints that carry the guarantees |
|---|---|---|
| `gl_account_mappings` | external_reference, erp_code, fineract_gl_account_id, status, attempts, last_error | unique external_reference |
| `clients` | client_uid, fineract_client_id, full_name | unique client_uid, unique fineract_client_id |
| `loans` | loan_reference, client_uid, fineract_loan_id, principal | unique loan_reference, unique fineract_loan_id |
| `idempotency_keys` | operation_type, external_reference, request_hash, status, response_status, response_body | unique (operation_type, external_reference) |
| `loan_transactions` | external_reference, operation_type, loan_reference, amount, transaction_date, state, fineract_transaction_id, fineract_breakdown, erp_entry_id, attempts, last_error | unique external_reference; partial unique index on loan_reference where operation_type = DISBURSEMENT and state <> FAILED |

Mock ERP (Alembic 0001): `accounts` (unique code, unique external_reference, request_hash),
`journal_entries` (unique external_reference, request_hash, metadata), `journal_lines` (amount > 0).
Balances are computed from lines at read time, never stored.

Fineract (Liquibase part 0242): `acc_gl_account.external_id VARCHAR(100)` with unique constraint
`acc_gl_account_external_id_UNIQUE`.

## State machine (Part J)

```
RECEIVED ──▶ FINERACT_PENDING ──▶ FINERACT_DONE ──▶ ERP_PENDING ──▶ COMPLETED
                 │                                        │
                 ▼                                        ▼
               FAILED                           NEEDS_RECONCILIATION
             (nothing reached Fineract;          (ERP conflict on the reference, mapping error,
              retry with the same reference,      or ERP unreachable after 10 attempts)
              even a corrected payload)
FAILED ──▶ RECEIVED  (the retry reuses the row)
```

| From | Allowed to | Trigger |
|---|---|---|
| RECEIVED | FINERACT_PENDING, FAILED | row created; about to call Fineract |
| FINERACT_PENDING | FINERACT_DONE, FAILED, NEEDS_RECONCILIATION | Fineract confirmed, or lookup by external id after a timeout/lock conflict found it; refused or unreachable → FAILED |
| FINERACT_DONE | ERP_PENDING | breakdown stored |
| ERP_PENDING | COMPLETED, NEEDS_RECONCILIATION | ERP accepted (or replayed); conflict/mapping error/attempts exhausted |
| FAILED | RECEIVED | caller retries the reference |

What is safe to retry, and who does it:

| Situation | Who retries | How |
|---|---|---|
| Fineract unreachable (FAILED) | the caller | same reference; the key is re-claimed; a corrected payload is accepted because nothing happened |
| Fineract timeout or lock 409 after sending | the service, immediately | never re-sends; `GET .../transactions/external-id/{ref}`; found → continue, not found → FAILED |
| ERP unreachable/timeout (ERP_PENDING) | recovery sweep | re-post the same reference; ERP is idempotent; up to 10 attempts then NEEDS_RECONCILIATION |
| crash after Fineract success (FINERACT_DONE) | recovery sweep on startup or `POST /admin/recover` | same as above |
| stale IN_PROGRESS keys for client creation / loan application | recovery sweep | released after 5 minutes; the retry re-checks Fineract by external id |

## GL sync design

- One row per ERP reference in `gl_account_mappings`; the unique constraint makes the table the lock.
- Transient Fineract failures retry inside one call with doubling backoff (3 attempts); FAILED rows are
  retried on the next call; 409/4xx are permanent until a human fixes the payload.
- A Fineract timeout after send is resolved by listing GL accounts and matching the external id.
- Layers against duplicates: SYNCED rows are never re-sent; Fineract's unique `external_id` plus the
  patched "identical replay returns existing" behaviour; the setup command refuses to create the loan
  product until every required reference is SYNCED.

## Observability

Both Python services log one JSON object per event through structlog. Every request binds
`correlation_id` (from `X-Correlation-ID` or generated, echoed back in the response header) and the path.
Events carry `external_reference`, `client_uid`, `loan_reference`, `fineract_transaction_id`,
`erp_transaction_id` and `status` where they apply, plus `timestamp`. The correlation id is forwarded to
Fineract and the ERP on every outbound call. DECISIONS.md shows how to trace one reference end to end.

## Security boundaries

- Partner API: `X-API-Key` on every `/api` and `/admin` route, enforced in middleware before routing.
- Fineract: the service runs as `integration-api`, a user whose role holds 15 permissions and no
  administration or delete rights. The `mifos` admin credentials exist only in the setup command.
- ERP: its own `X-API-Key`; only the Integration Service holds it.
- Databases: one user per service; no service can read another's tables.
