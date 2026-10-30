"""Parts C/E: loan application and approval. loan_reference is issued here (LN-YYYY-NNNNNN) and
stored as the Fineract loan externalId, so every later call addresses the loan by that reference."""

from datetime import date
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.fineract import FineractLoanGateway, FineractTimeoutError
from app.config import get_settings
from app.errors import ConflictError, NotFoundError, UnprocessableError
from app.models import Client, Loan
from app.services.fineract_dates import DATE_FORMAT, LOCALE, from_fineract, to_fineract

log = structlog.get_logger()


_PRODUCT_IDS: dict[str, int] = {}


def _product_id(fineract: FineractLoanGateway, name: str) -> int:
    if name not in _PRODUCT_IDS:
        found = fineract.find_loan_product_id(name, "product-lookup")
        if found is None:
            raise UnprocessableError(f"loan product {name!r} not found in Fineract; run setup")
        _PRODUCT_IDS[name] = found
    return _PRODUCT_IDS[name]


def product_id(fineract: FineractLoanGateway) -> int:
    return _product_id(fineract, get_settings().loan_product_name)


def issue_loan_reference(
    session: Session, client_uid: str, principal: Decimal, product_name: str
) -> Loan:
    """Insert first to obtain a sequence number, then derive the reference from it."""
    loan = Loan(
        loan_reference="PENDING",
        client_uid=client_uid,
        principal=principal,
        product_name=product_name,
    )
    session.add(loan)
    session.flush()
    loan.loan_reference = f"LN-{date.today().year}-{loan.id:06d}"
    session.commit()
    return loan


def fineract_loan_payload(
    fineract_client_id: int,
    product: int,
    loan_reference: str,
    principal: Decimal,
    submitted_on: date,
    disbursement_on: date,
) -> dict[str, Any]:
    return {
        "clientId": fineract_client_id,
        "productId": product,
        "externalId": loan_reference,
        "principal": int(principal),  # RWF: no minor unit
        "loanTermFrequency": 12,
        "loanTermFrequencyType": 2,
        "numberOfRepayments": 12,
        "repaymentEvery": 1,
        "repaymentFrequencyType": 2,
        "interestRatePerPeriod": 18,
        "interestType": 0,
        "interestCalculationPeriodType": 1,
        "amortizationType": 1,
        "transactionProcessingStrategyCode": "mifos-standard-strategy",
        "loanType": "individual",
        "submittedOnDate": to_fineract(submitted_on),
        "expectedDisbursementDate": to_fineract(disbursement_on),
        "locale": LOCALE,
        "dateFormat": DATE_FORMAT,
    }


def apply_and_approve(
    session: Session,
    fineract: FineractLoanGateway,
    client_uid: str,
    principal: Decimal,
    submitted_on: date,
    disbursement_on: date,
    approve: bool,
    cid: str,
) -> Loan:
    client = session.scalar(select(Client).where(Client.client_uid == client_uid))
    if client is None or client.fineract_client_id is None:
        raise NotFoundError(f"client {client_uid} not found")
    product = product_id(fineract)
    loan = issue_loan_reference(session, client_uid, principal, get_settings().loan_product_name)
    bound = log.bind(loan_reference=loan.loan_reference, client_uid=client_uid)
    fineract_loan_id: int
    try:
        fineract_loan_id = fineract.create_loan(
            fineract_loan_payload(
                client.fineract_client_id,
                product,
                loan.loan_reference,
                principal,
                submitted_on,
                disbursement_on,
            ),
            cid,
        )
    except FineractTimeoutError:
        # sent, response lost: Fineract may hold the loan under our reference; look before retrying
        found = fineract.get_loan_by_external_id(loan.loan_reference, cid)
        if found is None:
            session.delete(loan)
            session.commit()
            raise
        fineract_loan_id = int(found["id"])
        bound.warning("loan adopted after fineract timeout", fineract_present=True)
    except Exception:
        session.delete(loan)
        session.commit()
        raise
    loan.fineract_loan_id = fineract_loan_id
    session.commit()
    bound.info("loan applied", status="SUBMITTED")
    if approve:
        fineract.loan_command_by_external_id(
            loan.loan_reference,
            "approve",
            {
                "approvedOnDate": to_fineract(submitted_on),
                "locale": LOCALE,
                "dateFormat": DATE_FORMAT,
            },
            cid,
        )
        bound.info("loan approved", status="APPROVED")
    return loan


def approve(
    session: Session,
    fineract: FineractLoanGateway,
    loan_reference: str,
    approved_on: date,
    cid: str,
) -> Loan:
    """Approve a submitted loan. Fineract refuses anything not pending approval; checked here first
    so the caller gets a clear 409 instead of a translated Fineract business-rule error."""
    loan = session.scalar(select(Loan).where(Loan.loan_reference == loan_reference))
    if loan is None:
        raise NotFoundError(f"loan {loan_reference} not found")
    remote = fineract.get_loan_by_external_id(loan_reference, cid)
    if remote is None:
        raise NotFoundError(f"loan {loan_reference} is mapped but missing in Fineract")
    status = remote.get("status", {}) or {}
    if not status.get("pendingApproval", False):
        raise ConflictError(
            f"loan {loan_reference} is not pending approval (status {status.get('value')})",
            [{"field": "loan_reference", "status": status.get("value")}],
        )
    fineract.loan_command_by_external_id(
        loan_reference,
        "approve",
        {"approvedOnDate": to_fineract(approved_on), "locale": LOCALE, "dateFormat": DATE_FORMAT},
        cid,
    )
    log.info(
        "loan approved",
        loan_reference=loan_reference,
        client_uid=loan.client_uid,
        status="APPROVED",
    )
    return loan


def loan_view(
    session: Session, fineract: FineractLoanGateway, loan_reference: str, cid: str
) -> dict[str, Any]:
    loan = session.scalar(select(Loan).where(Loan.loan_reference == loan_reference))
    if loan is None:
        raise NotFoundError(f"loan {loan_reference} not found")
    remote = fineract.get_loan_by_external_id(loan_reference, cid)
    if remote is None:
        raise NotFoundError(f"loan {loan_reference} is mapped but missing in Fineract")
    status = remote.get("status", {})
    summary = remote.get("summary", {}) or {}
    transactions = [
        {
            "external_reference": t.get("externalId"),
            "type": (t.get("type") or {}).get("value"),
            "date": from_fineract(t.get("date")),
            "amount": t.get("amount"),
            "principal": t.get("principalPortion"),
            "interest": t.get("interestPortion"),
            "fees": t.get("feeChargesPortion"),
            "penalties": t.get("penaltyChargesPortion"),
            "outstanding_after": t.get("outstandingLoanBalance"),
            "reversed": bool(t.get("manuallyReversed")),
        }
        for t in remote.get("transactions", []) or []
    ]
    return {
        "loan_reference": loan_reference,
        "client_uid": loan.client_uid,
        "product": loan.product_name,
        "principal": loan.principal,
        "status": {
            "code": status.get("code"),
            "value": status.get("value"),
            "active": status.get("active"),
            "closed_obligations_met": status.get("closedObligationsMet"),
        },
        "balances": {
            "principal_disbursed": summary.get("principalDisbursed"),
            "principal_paid": summary.get("principalPaid"),
            "principal_outstanding": summary.get("principalOutstanding"),
            "interest_charged": summary.get("interestCharged"),
            "interest_paid": summary.get("interestPaid"),
            "interest_outstanding": summary.get("interestOutstanding"),
            "total_expected_repayment": summary.get("totalExpectedRepayment"),
            "total_repayment": summary.get("totalRepayment"),
            "total_outstanding": summary.get("totalOutstanding"),
        },
        "transactions": transactions,
    }
