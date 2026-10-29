"""Part B: the Fineract environment, as data. Every decision here is explained in
fineract/configuration/README.md. The setup command (app.setup) applies it idempotently."""

from typing import Any

# ERP references the loan product needs, by Fineract mapping role -> ERP external_reference.
# Every one must be SYNCED (Part A) before the product can be created: "order matters".
LOAN_PRODUCT_GL_MAPPINGS: dict[str, str] = {
    # cash-based accounting (accountingRule 2)
    "fundSourceAccountId": "GL-1100",  # Bank: money leaves on disbursement, returns on repayment
    "loanPortfolioAccountId": "GL-1200",  # Loan Receivable
    "transfersInSuspenseAccountId": "GL-1300",
    "interestOnLoanAccountId": "GL-4000",  # Interest Income
    "incomeFromFeeAccountId": "GL-4100",
    "incomeFromPenaltyAccountId": "GL-4200",
    "incomeFromRecoveryAccountId": "GL-4300",
    "writeOffAccountId": "GL-5100",
    "overpaymentLiabilityAccountId": "GL-2100",
    # charge-off and goodwill mappings Fineract 1.15 expects on a cash-based product; no such
    # transactions are produced in this assessment, mapped to the nearest income/expense account
    "incomeFromChargeOffInterestAccountId": "GL-4000",
    "incomeFromChargeOffFeesAccountId": "GL-4100",
    "incomeFromChargeOffPenaltyAccountId": "GL-4200",
    "incomeFromGoodwillCreditInterestAccountId": "GL-4000",
    "incomeFromGoodwillCreditFeesAccountId": "GL-4100",
    "incomeFromGoodwillCreditPenaltyAccountId": "GL-4200",
    "goodwillCreditAccountId": "GL-5100",
    "chargeOffExpenseAccountId": "GL-5100",
    "chargeOffFraudExpenseAccountId": "GL-5100",
}

DATE_FORMAT = "dd MMMM yyyy"
LOCALE = "en"


def loan_product_payload(
    name: str, short_name: str, currency: str, gl_ids: dict[str, int]
) -> dict[str, Any]:
    """SME Asset Finance: 18% p.a. declining balance, 12 monthly equal instalments, single
    disbursement, cash-based accounting posting to the synced GL accounts."""
    missing = sorted(set(LOAN_PRODUCT_GL_MAPPINGS.values()) - set(gl_ids))
    if missing:
        raise ValueError(f"missing GL account ids for mappings: {missing}")
    return {
        "name": name,
        "shortName": short_name,
        "description": "SME asset financing, 18% p.a. declining balance, 12 monthly instalments",
        "currencyCode": currency,
        "digitsAfterDecimal": 0,  # RWF has no minor unit (m_currency.decimal_places = 0)
        "inMultiplesOf": 0,
        "locale": LOCALE,
        "dateFormat": DATE_FORMAT,
        # amounts
        "principal": 5000000,
        "minPrincipal": 100000,
        "maxPrincipal": 50000000,
        # term: 12 monthly repayments
        "numberOfRepayments": 12,
        "minNumberOfRepayments": 1,
        "maxNumberOfRepayments": 36,
        "repaymentEvery": 1,
        "repaymentFrequencyType": 2,  # months
        # interest: 18% per year, declining balance, equal instalments
        "interestRatePerPeriod": 18,
        "interestRateFrequencyType": 3,  # per year
        "interestType": 0,  # declining balance
        "amortizationType": 1,  # equal instalments
        "interestCalculationPeriodType": 1,  # same as repayment period
        "allowPartialPeriodInterestCalculation": False,
        "daysInYearType": 365,
        "daysInMonthType": 30,
        "isInterestRecalculationEnabled": False,
        # single disbursement
        "multiDisburseLoan": False,
        "loanScheduleType": "CUMULATIVE",
        "transactionProcessingStrategyCode": "mifos-standard-strategy",
        "inArrearsTolerance": 0,
        "overdueDaysForNPA": 90,
        # cash-based accounting (2): income is recognised when cash is received
        "accountingRule": 2,
        **{role: gl_ids[ref] for role, ref in LOAN_PRODUCT_GL_MAPPINGS.items()},
    }


# Least privilege for the Integration Service: only what Parts A to K call.
API_ROLE_PERMISSIONS: tuple[str, ...] = (
    "READ_OFFICE",
    "READ_CURRENCY",
    "READ_PAYMENTTYPE",
    "READ_CLIENT",
    "CREATE_CLIENT",
    "ACTIVATE_CLIENT",
    "READ_LOANPRODUCT",
    "READ_LOAN",
    "CREATE_LOAN",
    "APPROVE_LOAN",
    "DISBURSE_LOAN",
    "REPAYMENT_LOAN",
    "READ_JOURNALENTRY",
    "READ_GLACCOUNT",
    "CREATE_GLACCOUNT",
)


def role_permissions_payload() -> dict[str, Any]:
    return {"permissions": {code: True for code in API_ROLE_PERMISSIONS}}


def api_user_payload(username: str, password: str, role_id: int, office_id: int) -> dict[str, Any]:
    return {
        "username": username,
        "firstname": "Integration",
        "lastname": "Service",
        "email": "integration-service@example.com",
        "officeId": office_id,
        "roles": [role_id],
        "sendPasswordToEmail": False,
        "password": password,
        "repeatPassword": password,
    }
