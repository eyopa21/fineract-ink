# Simulations

Every simulation is scripted and re-runnable against the stack started by `make up`. Each prints the
nine-point evidence block (initial state, request, response, Fineract state, ERP state, integration state,
expected, actual, explanation) and its transcript is saved under `evidence/`. Screenshots are not used.

| Make target | Covers | Script | Evidence |
|---|---|---|---|
| `make demo-part-a` | Part A behaviours 1 to 4 (Java) | `scripts/demo-part-a.sh` | `evidence/part-a/demo-part-a.txt` |
| `make demo-gl-sync` | Part A Python side: sync 4x | `scripts/demo-gl-sync.sh` | `evidence/part-a/gl-sync-demo.txt` |
| `make demo-part-g` | Part G Mock ERP | `scripts/demo-part-g.sh` | `evidence/part-g/demo-part-g.txt` |
| `make setup` | Part B environment, idempotent | `app.setup` | `evidence/part-b/` |
| `make simulate-lifecycle` | Part D | `scripts/demo-part-d.sh` | `evidence/part-d/` |
| `make simulate-duplicates` | I1 | `tests_live/test_concurrency.py -k i1` | `evidence/part-i/concurrency.txt` |
| `make simulate-concurrency` | I1, I2a, I2b, I3 | `tests_live/test_concurrency.py` | `evidence/part-i/concurrency.txt` |
| `make simulate-failures` | J1 to J5 | `scripts/simulate_failures.py` | `evidence/part-j/failures.txt` |
| `make simulate-reconciliation` | Part K | `scripts/simulate_reconciliation.py` | `evidence/part-k/reconciliation.txt` |
| `make simulate-gl-reconciliation` | Stretch: GL master data | `scripts/simulate_gl_reconciliation.sh` | `evidence/part-l/gl-reconciliation.txt` |

The figures below are from the saved transcripts.

## Part A: ERP-owned GL accounts in Fineract

1. **Initial state:** no GL account carries external reference `GL-DEMO-15975`.
2. **Request:** `POST /glaccounts` with `externalId` four times with the same payload, once with a changed
   name, once as a user whose role has no permissions.
3. **Response:** 200 `{"resourceId":1}`; then 200 x3 with `changes.idempotentReplay=true` and the same id;
   then 409 `error.msg.glaccount.externalid.conflict`; then 403 naming `CREATE_GLACCOUNT`.
4. **Fineract state:** one row in `acc_gl_account` with `external_id = GL-DEMO-15975`; constraint
   `acc_gl_account_external_id_UNIQUE` present.
5. **ERP state:** not involved.
6. **Integration state:** not involved.
7. **Expected:** reference persisted and unique; identical replay returns existing; different payload 409;
   permission enforced.
8. **Actual:** as expected.
9. **Explanation:** the write service looks the reference up inside the handler's transaction; the unique
   index backs it under concurrency; the command pipeline checks `CREATE_GLACCOUNT` before dispatch.

## Part A (Python): GL sync four times

1. **Initial state:** 12 ERP accounts, 0 mapping rows, 2 ERP-synced accounts in Fineract.
2. **Request:** `POST /api/accounts/sync` four times.
3. **Response:** first call created 11, failed 1 (GL-1200 held by a stray manual account, 409 from the
   patch); calls 2 to 4: already_synced 11, failed 1, created 0.
4. **Fineract state:** 10 ERP-synced accounts after the first call, unchanged by calls 2 to 4.
5. **ERP state:** 12 accounts, unchanged.
6. **Integration state:** 12 mapping rows, 11 SYNCED, 1 FAILED with Fineract's message in `last_error`.
7. **Expected:** one ERP account, one GL account, one mapping per reference; FAILED retried, not duplicated.
8. **Actual:** as expected; after the stray account was removed the FAILED row became SYNCED on the next
   call (`evidence/part-b/setup-report.json`).
9. **Explanation:** SYNCED rows are never re-sent; the conflict is permanent until the payload or Fineract
   changes; Fineract's unique `external_id` is the backstop.

## Part G: Mock ERP

1. **Initial state:** 9 seeded accounts, balances zero.
2. **Request:** account posted 4x, then with a changed name, then with a reused code; journal entry
   unbalanced, then balanced 4x, then with a changed amount; faults `down` and `timeout_after_write`.
3. **Response:** 201, 200 x3, 409, 409; 422, 201, 200 x3 (same `entry_id`), 409; 503; 201 then 200 replay.
4. **Fineract state:** not involved.
5. **ERP state:** one account per reference; one entry per reference; balances 1200 DR 5,000,000 /
   1100 CR 5,000,000 after one entry applied once; one entry despite the post-write timeout.
6. **Integration state:** not involved.
7. **Expected:** DB-enforced uniqueness, balanced entries only, idempotent replays, faults on demand.
8. **Actual:** as expected.
9. **Explanation:** canonical request hash stored per row decides replay versus conflict; balances are
   derived from lines at read time.

## Part B: Fineract environment

1. **Initial state:** default Head Office, USD only, no loan product, no API user.
2. **Request:** `python -m app.setup` (via `make setup-local`), twice.
3. **Response:** first run `status: OK`, product id 1, role id 7 with 15 permissions, user id 8;
   verification `read_loan_product: 200`, `read_users_denied: 403`. Second run: every `changed: false`.
4. **Fineract state:** `evidence/part-b/fineract-state.txt`: Kigali Main Office, RWF enabled, SME Asset
   Finance 18 % p.a. declining balance, 12 monthly instalments, cash-based, 18 mappings all pointing at
   ERP-synced accounts.
5. **ERP state:** unchanged.
6. **Integration state:** 9 required references SYNCED.
7. **Expected:** ordered, idempotent, product mapped only to synced accounts.
8. **Actual:** as expected; an earlier run with GL-1200 FAILED halted before creating the product
   (`evidence/part-b/setup-halted-on-failed-gl-sync.txt`).
9. **Explanation:** the setup refuses to build the product until every mapping reference is SYNCED.

## Part D: loan lifecycle

1. **Initial state:** new client `SME-283337`, no loan.
2. **Request:** application + approval 5,000,000 RWF; disbursement; repayments 458,333 x3; payoff of
   Fineract's `total_outstanding`.
3. **Response:** 201 Approved; 200 COMPLETED for each transaction with Fineract's breakdown.
4. **Fineract state:** after disbursement principal 5,000,000 outstanding, interest 500,799; after R1
   4,616,667 / 425,799 (split 383,333 + 75,000); R2 389,084 + 69,249; R3 394,921 + 63,412; payoff
   3,832,662 + 293,138; status Closed (obligations met), total repaid 5,500,799.
5. **ERP state:** entries 9 to 13, one per transaction, lines equal to Fineract's split.
6. **Integration state:** five transactions COMPLETED with Fineract ids 1, 3, 4, 5, 6.
7. **Expected:** real Fineract status after every stage, ends Closed, journal derived from Fineract.
8. **Actual:** as expected; raw Fineract loan JSON in `evidence/part-d/fineract-loan-LN-2026-000001.json`.
9. **Explanation:** every amount in the ERP comes from the breakdown Fineract returned for that
   transaction; the service computed no interest.

## Part I: duplicates and concurrency (automated, 12 threads, real HTTP)

### I1 sequential duplicates
1. Loan Approved, 0 disbursements. 2. Same disbursement 4x. 3. 200 x4, three flagged replay.
4. Fineract disbursements 1 (id 27). 5. ERP entries 1 (entry 29). 6. Integration transactions 1, COMPLETED.
7. Expected 4 / 1 / 1 / 1. 8. Actual 4 / 1 / 1 / 1. 9. Idempotency key replays the stored response.

### I2a concurrent, same reference
1. Loan Approved. 2. 12 simultaneous disbursements, one reference. 3. First wave 1 x 200 + 11 x 202;
after settling 12 x 200. 4. Fineract 1. 5. ERP 1. 6. Integration 1. 7./8. Exactly one. 9. The unique
constraint on `idempotency_keys` admits one INSERT; the rest re-read and get 202 until the winner finishes.

### I2b concurrent, different references
1. Loan Approved. 2. 12 simultaneous disbursements, 12 references. 3. 1 x 200 + 11 x 409, all 11 refused
by the per-loan partial unique index before Fineract was called. 4. Fineract 1. 5. ERP 1. 6. One COMPLETED
row, no other rows. 7./8. Exactly one. 9. Layers: idempotency key (same reference only), per-loan DB index
(this case), Fineract's unique transaction externalId, Fineract's loan state (last line of defence, holds
even with an empty integration database).

### I3 concurrent repayments
1. Loan Active, `totalOutstanding` 5,500,799. 2. 10 simultaneous repayments with different references
(100,000 to 109,000) plus 3 duplicates of one. 3. First wave 3 x 200, 3 x 202, 7 x 503 (Fineract
optimistic-lock losers, nothing recorded); a settling pass re-sent every reference once, re-executing 7,
replaying the rest. 4. Fineract repayments 10, recorded sum 1,045,000, `totalOutstanding` 4,455,799.
5. ERP entries 10. 6. Ten COMPLETED. 7. final = initial − Σ Fineract amounts = 4,455,799. 8. 4,455,799.
9. Each reference applied once; duplicates applied once; the balance moved by what Fineract recorded.

## Part J: failure handling

| # | Scenario | Response | State left | Recovery | After |
|---|---|---|---|---|---|
| 1 | ERP down after Fineract success | 202 ERP_PENDING; duplicate 202 IN_PROGRESS | FINERACT_DONE with Fineract id 80 | sweep re-posts the reference | COMPLETED, entry 78 once, replay 200 |
| 2 | Fineract down | 503 "nothing was recorded; safe to retry" | FAILED | caller re-sends the same reference | COMPLETED, 1 repayment |
| 3 | Fineract timeout after send | 200 COMPLETED | none | lookup by external id, no re-send | 1 repayment, id 101 |
| 4 | ERP timeout after write | 202 after 10.9 s | ERP_PENDING, entry 84 already in ERP | sweep re-posts, ERP replays | COMPLETED, entry 84 once |
| 5 | Crash after Fineract success | 500 | FINERACT_DONE | process restarted, startup sweep | COMPLETED, entry 86, replay 200 |

Each block in `evidence/part-j/failures.txt` carries the nine points. Retry policy and allowed transitions:
ARCHITECTURE.md, state machine section.

## Part K: reconciliation

1. **Initial state:** loan `LN-2026-000035` disbursed and repaid once through the service.
2. **Request:** seeded a crash-after-Fineract repayment (R2), a direct Fineract repayment of 150,000 with
   a direct ERP entry of 140,000 under the same reference (R3), and an ERP-only entry (GHOST); then
   `POST /api/reconciliation {loan_references}`, then with `repair: true`, twice.
3. **Response:** summary MATCHED 2, MISSING_IN_ERP 1, MISSING_IN_FINERACT 1, AMOUNT_MISMATCH 1; after
   repair MATCHED 3, MISSING_IN_ERP 0, the other two unchanged; second repair identical.
4. **Fineract state:** DISB, R1, R2, R3 present; GHOST absent.
5. **ERP state:** DISB, R1, R3 (140,000), GHOST present; R2 absent, then entry 91 after repair.
6. **Integration state:** R2 FINERACT_DONE → COMPLETED after repair; R3 and GHOST unknown to the service.
7. **Expected:** all four outcomes keyed by reference; read-only by default; repair explicit, idempotent,
   limited to what the service recorded.
8. **Actual:** as expected (`evidence/part-k/reconciliation.txt`).
9. **Explanation:** mismatches and ERP-only entries were not produced by this service, so a human decides;
   the missing entry is re-posted from the stored Fineract breakdown and the ERP replays on the reference.

## Stretch: GL master-data reconciliation

1. **Initial state:** the ERP chart (9 seeded accounts plus demo accounts) synced; Fineract also holds GL
   accounts created outside the sync during manual testing and the Part A integration tests.
2. **Request:** `POST /api/reconciliation/gl-accounts`.
3. **Response:** MATCHED 13, MISSING_IN_ERP 7, MISSING_IN_FINERACT 0, ATTRIBUTE_MISMATCH 0.
4. **Fineract state:** 20 GL accounts; the 7 reported carry external references the ERP never issued
   (`GL-DEMO-15975`, `GL-DPH6TG`, ...), i.e. created by the Part A demo and tests directly in Fineract.
5. **ERP state:** 13 accounts, all present in Fineract with the same code, name and type.
6. **Integration state:** every ERP-owned account has a SYNCED mapping row; the 7 strays have none.
7. **Expected:** ERP-owned accounts match; accounts that bypassed the sync surface as MISSING_IN_ERP.
8. **Actual:** as expected (`evidence/part-l/gl-reconciliation.txt`).
9. **Explanation:** the external reference is the key on both sides; an account without one, or with one
   the ERP does not know, cannot have come through the sync. Repair of MISSING_IN_FINERACT is the
   create-only sync; MISSING_IN_ERP is a human decision (delete in Fineract or register in the ERP).
