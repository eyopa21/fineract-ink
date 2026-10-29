"""Seed chart of accounts owned by the ERP.

The three the assessment names plus the accounts a cash-based Fineract loan product must map
(fees, penalties, recoveries, write-off, overpayments, transfers in suspense). Every account is
synced to Fineract through the Integration Service; the loan product never references anything else.
"""

from app.models import AccountType

CURRENCY = "RWF"

SEED_ACCOUNTS: list[dict[str, str]] = [
    {"code": "1100", "name": "Bank", "type": AccountType.ASSET},
    {"code": "1200", "name": "Loan Receivable", "type": AccountType.ASSET},
    {"code": "1300", "name": "Transfers in Suspense", "type": AccountType.ASSET},
    {"code": "2100", "name": "Overpayment Liability", "type": AccountType.LIABILITY},
    {"code": "4000", "name": "Interest Income", "type": AccountType.INCOME},
    {"code": "4100", "name": "Fee Income", "type": AccountType.INCOME},
    {"code": "4200", "name": "Penalty Income", "type": AccountType.INCOME},
    {"code": "4300", "name": "Recoveries on Written-off Loans", "type": AccountType.INCOME},
    {"code": "5100", "name": "Loan Write-off Expense", "type": AccountType.EXPENSE},
]


def external_reference_for(code: str) -> str:
    return f"GL-{code}"
