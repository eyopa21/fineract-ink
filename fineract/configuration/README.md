# Part B: Fineract environment

Applied by `make setup` (inside the stack) or `make setup-local` (Fineract on localhost). The command is
`python -m app.setup` in the Integration Service; it is idempotent and prints a JSON report that is saved to
`evidence/part-b/setup-report.json`. The specification lives in one place,
`integration-service/app/setup_spec.py`; this file explains the decisions.

## Order of operations (it matters)

1. Office and currency.
2. GL sync (Part A): the ERP chart of accounts is pushed to Fineract. Setup stops if any account the loan
   product needs is not `SYNCED`, so the product can never reference an account that did not come from the ERP.
3. Loan product, mapped to the Fineract ids recorded by the sync. No GL id is hard-coded anywhere.
4. Role with least privilege, then the API user holding that role.
5. Verification with the API user's own credentials: it can read the loan product; it cannot list users.

## Decisions

| Item | Decision | Why |
|---|---|---|
| Office | Rename the default Head Office (id 1) to **Kigali Main Office** | One office keeps every client, loan and user under the same hierarchy. Creating a child office would leave the default "Head Office" in place and force every call to pass an office id. |
| Currency | Enable **RWF** in the organisation's allowed currencies, keeping any already enabled | Fineract refuses a product in a currency that is not enabled. RWF has `decimal_places = 0` in Fineract's currency table, so the product uses `digitsAfterDecimal = 0` and all amounts are whole francs. |
| Loan product | **SME Asset Finance**, short name `SMEA`, RWF | As suggested by the assessment. |
| Interest | 18 % per year, **declining balance**, equal instalments, interest calculated per repayment period | Declining balance is the normal basis for term loans and gives a real principal/interest split per instalment, which Part H derives journal entries from. Flat interest would make the split artificial. |
| Term | 12 monthly repayments, `repaymentEvery = 1` month, `daysInYear = 365`, `daysInMonth = 30` | Suggested term. 365/30 keeps monthly interest at exactly 18 %/12 regardless of calendar month length, which makes the evidence easy to check by hand. |
| Disbursement | Single disbursement (`multiDisburseLoan = false`), schedule type `CUMULATIVE` | Multi-tranche is explicitly out of scope. |
| Amounts | default 5,000,000; min 100,000; max 50,000,000 | Suggested loan is 5,000,000 RWF; the range leaves room for other test loans. |
| Repayment strategy | `mifos-standard-strategy` (penalties, fees, interest, principal) | Fineract's default; the Part H mapping reads the actual breakdown from Fineract, so the strategy only decides the split, never the journal logic. |
| Accounting | **Cash-based** (`accountingRule = 2`) | Part H wants journal entries derived from Fineract's repayment breakdown: DR Bank / CR Loan Receivable (principal) / CR Interest Income (interest). Cash-based accounting recognises interest exactly when cash arrives, so Fineract's own journal and the ERP's agree line for line and reconciliation (Part K) is a direct comparison. Accrual would recognise interest on a schedule independent of payments, creating timing differences with no benefit for this scenario. |
| GL mappings | Bank 1100, Loan Receivable 1200, Transfers in Suspense 1300, Overpayment Liability 2100, Interest Income 4000, Fee Income 4100, Penalty Income 4200, Recoveries 4300, Write-off Expense 5100 | Fineract requires every cash-based mapping to be set even when unused. Each role gets its own account so a wrong posting is visible instead of hidden in a shared account. Charge-off and goodwill mappings point at the nearest income/expense account; no such transactions occur here. |
| Fees, penalties, taxes | None | Not required; stretch item. |
| API user | username `integration-api`, role `integration-service` | The service never uses the `mifos` super user at runtime. The role holds only: READ_OFFICE, READ_CURRENCY, READ_PAYMENTTYPE, READ_CLIENT, CREATE_CLIENT, ACTIVATE_CLIENT, READ_LOANPRODUCT, READ_LOAN, CREATE_LOAN, APPROVE_LOAN, DISBURSE_LOAN, REPAYMENT_LOAN, READ_JOURNALENTRY, READ_GLACCOUNT, CREATE_GLACCOUNT. No delete, no user administration, no configuration. Permissions are re-applied declaratively on every setup run. |
| Admin credentials | `mifos`/`password` used only by the setup command | Kept in `FINERACT_ADMIN_*` variables so the runtime configuration cannot escalate. |
| Password of the API user | fixed in configuration for this local assessment | A real deployment would inject it from a secret store; noted as a known weakness. |

## Verification

`make setup` prints the report. Look for `"status": "OK"`, `gl_sync.failed = 0`, the loan product id, and
`verification.read_loan_product = 200` with `verification.read_users_denied = 403`.
