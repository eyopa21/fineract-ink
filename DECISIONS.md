# Decisions

Trade-offs, rationale, assumptions, and what was deliberately left out. Where a decision is explained in
more depth elsewhere, the file is named.

## 1. Fineract customization mechanism

**Chosen:** extend the existing `POST /glaccounts` command path inside Fineract 1.15.0 (entity, deserializer,
write service, read service, a new exception and JAX-RS mapper, a Liquibase part). Delivered as a patch
against the upstream tag applied to a git submodule and baked into `fineract-custom:1.15.0`.

**Rejected:** a new endpoint (would need a new permission and leave the existing endpoint able to create
duplicates); a `custom/` module (cannot add a column to a core entity or change the write service, which is
where uniqueness and the idempotent decision have to live); relying on Fineract's `Idempotency-Key` header
(keyed on the command, not on the ERP reference, so a second sync run with a new key still duplicates).

**Transaction boundary:** `CreateGLAccountCommandHandler.processCommand` is `@Transactional`; the lookup,
the comparison and the insert run in one transaction. The unique index on `external_id` is the last line of
defence when two identical creates race; the loser gets 409 and succeeds with 200 on retry.

Full write-up: `fineract-customization/README.md`. Browsable diff on a fork branch:
[1.15.0...feat/add-externalId-to-gl-accounts-module](https://github.com/eyopa21/fineract/compare/1.15.0...eyopa21:fineract:feat/add-externalId-to-gl-accounts-module?expand=1).

## 2. Sync versus async, and why there is no queue or worker

Every partner call is synchronous end to end: Fineract, then the ERP, then the response. A queue would add a
component to run, secure and reason about, and would not remove the hard problem, which is knowing whether
a call that got no response actually happened. That problem is solved by persisted state plus lookups by
external id, and the same mechanism gives recovery: a startup sweep and `POST /admin/recover` finish any
row that an outage or crash left mid-way. The caller sees 202 while the ERP leg is pending and the
idempotency key keeps answering 202 for that reference until it is settled.

The cost is latency under outage (the caller waits for timeouts rather than getting an instant 202) and no
horizontal scaling of the ERP leg. For an SME lending integration with low volume that is the right trade.

## 3. Database constraints are the mechanism, not the application

| Guarantee | Constraint |
|---|---|
| one execution per business operation | unique `(operation_type, external_reference)` on `idempotency_keys`; the INSERT is the claim |
| one client per `client_uid` | unique `clients.client_uid` |
| one loan per `loan_reference` | unique `loans.loan_reference` |
| one disbursement per loan | partial unique index on `loan_transactions.loan_reference` where DISBURSEMENT and state <> FAILED |
| one transaction per reference | unique `loan_transactions.external_reference` |
| one GL mapping per ERP reference | unique `gl_account_mappings.external_reference` |
| Fineract side | unique `acc_gl_account.external_id` (patch); Fineract's own unique client, loan and transaction external ids |
| ERP side | unique `accounts.code`, `accounts.external_reference`, `journal_entries.external_reference`; `amount > 0`; balanced entries |

Idempotency held only in application memory would vanish on restart and would not survive two replicas;
every claim here is a row.

## 4. Idempotency semantics

The decision table is in `integration-service/app/idempotency.py`. Two choices worth defending:

- **A FAILED key accepts a corrected payload.** FAILED means nothing happened in Fineract (the request never
  reached it, or Fineract rejected it). Forcing a new reference would punish the caller for a validation
  mistake. Once anything did execute the key is COMPLETED or IN_PROGRESS and a different payload is 409.
- **202 does not complete the key.** While the ERP leg is pending, replays return 202 with the current
  transaction state rather than a stale body. The recovery sweep completes the key with the final body when
  the transaction settles, so the "same body" rule of the assessment holds for finished operations.

Concurrency: same reference, N parallel requests: exactly one INSERT wins the unique constraint, the rest
re-read and get 202 or the replay. Different references on one loan: the partial unique index refuses all
but one at INSERT, before Fineract is called (11 of 12 refused that way in the Part I run).

## 5. Retries, and what is never retried blindly

| Call | Policy |
|---|---|
| Fineract unreachable | not retried by the service; FAILED, caller retries the same reference |
| Fineract timeout after send, or 409 lock conflict | never re-sent; `GET .../transactions/external-id/{ref}`; found → continue, not found → FAILED |
| ERP post | immediate attempt, then the recovery sweep; up to 10 attempts then NEEDS_RECONCILIATION |
| GL sync to Fineract | 3 attempts with backoff 0.5 s, 1 s, 2 s inside one call; FAILED rows retried on the next call; 409/4xx permanent |
| loan application timeout | the loan is looked up by `loan_reference` and adopted if Fineract created it |

The red flag in the brief, "retrying a financial call without first checking whether it happened", is
avoided by making our reference the Fineract external id on every transaction.

## 6. Partial failure

The state machine (`app/services/transactions.py`, ARCHITECTURE.md) writes a row before each external call
and after each result. The only states a crash can leave are therefore ones the sweep knows: RECEIVED or
FINERACT_PENDING (look up in Fineract), FINERACT_DONE or ERP_PENDING (re-post to the ERP). Five scenarios
are scripted in Part J; all five recover with exactly one Fineract transaction and one ERP entry.

NEEDS_RECONCILIATION is reserved for cases a machine must not guess: an ERP conflict on our reference (the
ERP holds a different entry under it), a breakdown that does not add up, or an ERP that stays down past the
attempt cap.

## 7. Accounting mapping (Part H)

Cash-based accounting on the product, so Fineract itself recognises interest when cash arrives, and the
ERP entries mirror Fineract's own journal line for line. The principal/interest/fee/penalty split comes
from Fineract's transaction (`principalPortion`, `interestPortion`, `feeChargesPortion`,
`penaltyChargesPortion`, `overpaymentPortion`); the mapping refuses a breakdown whose portions do not sum
to the amount rather than inventing a split.

Overpayments: Fineract reports an `overpaymentPortion`; it is credited to Overpayment Liability (2100).
Reversals: a reversed Fineract transaction is mirrored by a second entry `<reference>-REV` with every line
flipped; reconciliation expects it. Reversal posting itself is not wired to a partner endpoint (stretch).

## 8. Reconciliation keying

Per Fineract disbursement or repayment carrying an external id, against the ERP entry with the same
reference, comparing amount and type. Fineract's internal accrual transactions have no external id and are
excluded: they are never posted to the ERP by design, so reporting them would be noise. Read-only by
default; repair only re-posts what this service itself recorded, from the stored Fineract breakdown, and
the ERP's idempotency makes repair safe to repeat.

## 9. Fineract environment (Part B)

Cash-based accounting, declining balance, 365/30 day counts, RWF with zero decimals, a least-privilege API
user. Every choice and its reason: `fineract/configuration/README.md`.

## 10. Mock ERP

FastAPI with Pydantic because validation (balanced entries, enum types, positive amounts) and OpenAPI come
for free and the whole service stays under 700 lines. Uniqueness lives in the database, not in the caller.
Balances are derived from lines at read time so they can never disagree with the entries. Negative balances
are allowed on purpose: reversals, refunds, overdrafts and contra accounts are legitimate; the ERP validates
structure and Fineract owns business rules.

## 11. Assumptions recorded during the work

- The default Head Office is renamed rather than a child office created, so every entity sits under one
  office.
- The loan product requires nine GL mappings in Fineract 1.15, not the three the brief names; the ERP seeds
  nine accounts and all come through the sync.
- `loan_reference` is `LN-<year>-<6-digit sequence>`; a rejected application consumes a sequence number.
- Repayment dates in the demos are in the past because Fineract refuses future-dated transactions.
- "Approval here or separate": both are offered, in the application call by default and as its own
  idempotent endpoint.

## 12. What was not built, and why

- A queue or worker: see 2.
- Fees, penalties, taxes, multi-tranche: out of scope by the brief; the mapping already carries fee and
  penalty portions.
- A partner endpoint for reversals and write-off: stretch; the mapping and reconciliation rules exist.
- Other stretch items (fees and penalties in the mapping, demonstrated reversal, dead-letter view,
  Prometheus metrics, scheduled reconciliation, signed webhooks): not done. GL master-data reconciliation
  is done (`POST /api/reconciliation/gl-accounts`).
- TLS inside the stack: Fineract runs on plain HTTP behind Docker's network; a Caddy proxy for browser UIs
  is provided as a local override.
- Pre-validation of the principal range in the service: Fineract's own message is surfaced as 422 instead.

## 13. Known weaknesses

- Credentials are fixed in configuration (`integration-api`, partner and ERP keys). A deployment would inject
  them from a secret store.
- Fault switches are process-local; with more than one replica each would need its own toggle.
- ERP listing filters by loan reference in Python over a date-bounded query; fine for a mock, not for scale.
- The recovery sweep runs on startup and on request only; no scheduler. Scheduled reconciliation is a
  stretch item.
- A Fineract optimistic-lock loser is reported as 503 and must be retried by the caller; the service does
  not queue it.
- Sequence gaps in `loan_reference` when an application is rejected.
- Live evidence in this repository was produced against the author's development stack; `make up` followed by
  the `make simulate-*` targets regenerates it on a clean compose stack.

## 14. "What happened to FIN-2026-000001?"

One API call and one log filter answer it. Say the partner's reference is `LN-2026-000001-R1`.

```bash
curl -s -H "X-API-Key: partner-demo-key" http://localhost:8100/api/transactions/LN-2026-000001-R1
```
```json
{"external_reference": "LN-2026-000001-R1", "operation": "REPAYMENT", "loan_reference": "LN-2026-000001",
 "amount": "458333", "transaction_date": "2026-02-15", "state": "COMPLETED", "fineract_transaction_id": 3,
 "fineract_breakdown": {"amount": "458333.0", "principal": "383333.0", "interest": "75000.0", "fees": "0", "penalties": "0", "overpayment": "0"},
 "erp_entry_id": 10, "attempts": 1, "last_error": null}
```

The state says where it stopped; `fineract_transaction_id` and `erp_entry_id` say what each system
assigned. Then the log trail, filtered on the reference (every line is JSON, from `make logs SVC=integration-service`
or `evidence/part-d/log-trail.jsonl`):

```json
{"external_reference": "LN-2026-000001-R1", "loan_reference": "LN-2026-000001", "status": "FINERACT_PENDING", "previous": "RECEIVED", "event": "transaction state", "correlation_id": "demo-d-283337", "path": "/api/loans/LN-2026-000001/repayments", "method": "POST", "level": "info", "timestamp": "2026-10-29T14:15:02.101Z"}
{"external_reference": "LN-2026-000001-R1", "loan_reference": "LN-2026-000001", "fineract_transaction_id": 3, "amount": "458333.0", "principal": "383333.0", "interest": "75000.0", "event": "fineract transaction recorded", "correlation_id": "demo-d-283337", ...}
{"external_reference": "LN-2026-000001-R1", "loan_reference": "LN-2026-000001", "status": "ERP_PENDING", "previous": "FINERACT_DONE", "event": "transaction state", ...}
{"external_reference": "LN-2026-000001-R1", "loan_reference": "LN-2026-000001", "fineract_transaction_id": 3, "erp_transaction_id": 10, "erp_replayed": false, "status": "COMPLETED", "event": "transaction completed", "correlation_id": "demo-d-283337", ...}
```

The `correlation_id` ties these to the partner's request and to the matching lines in the ERP's log
(`make logs SVC=mock-erp`), where the same id appears on the journal entry post. If the state were
ERP_PENDING or NEEDS_RECONCILIATION, `last_error` holds the reason and `POST /api/reconciliation` with the
reference shows what each system holds.

## 15. AI assistance

Claude (Anthropic) was used throughout as a pair-programming assistant: exploring the Fineract source,
drafting code, tests, scripts and these documents. Every design decision above was reviewed and is
explainable live; the test suites and evidence transcripts were produced by running the code, not written
by hand.
