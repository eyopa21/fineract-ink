"""Parts D, E, F: loan application, disbursement, repayments, loan view."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.erp import ErpGateway
from app.clients.fineract import FineractLoanGateway
from app.db import get_session
from app.deps import get_erp, get_fineract
from app.middleware import correlation_id_of
from app.models import Loan, OperationType
from app.routers.idempotent import run_idempotent
from app.schemas import (
    DisbursementRequest,
    InProgressOut,
    LoanApplication,
    LoanApproval,
    LoanApproved,
    LoanCreated,
    LoanOut,
    RepaymentRequest,
    TransactionOut,
)
from app.services.loans import apply_and_approve, approve, loan_view
from app.services.transactions import execute, get_transaction, tx_view

router = APIRouter(prefix="/api/loans", tags=["loans"])
SessionDep = Annotated[Session, Depends(get_session)]
FineractDep = Annotated[FineractLoanGateway, Depends(get_fineract)]
ErpDep = Annotated[ErpGateway, Depends(get_erp)]


@router.post(
    "",
    response_model=LoanCreated | InProgressOut,
    status_code=201,
    responses={200: {"model": LoanCreated}, 202: {}, 409: {}},
)
def post_loan(
    payload: LoanApplication,
    request: Request,
    response: Response,
    session: SessionDep,
    fineract: FineractDep,
) -> dict[str, Any]:
    """Loan application (and approval unless approve=false). Issues loan_reference LN-YYYY-NNNNNN,
    stored as the Fineract loan externalId. Idempotent on external_reference."""
    cid = correlation_id_of(request)

    def action() -> tuple[int, dict[str, Any]]:
        loan = apply_and_approve(
            session,
            fineract,
            payload.client_uid,
            payload.principal,
            payload.submitted_on,
            payload.expected_disbursement_date,
            payload.approve,
            cid,
        )
        return 201, {
            "loan_reference": loan.loan_reference,
            "client_uid": loan.client_uid,
            "principal": loan.principal,
            "status": "APPROVED" if payload.approve else "SUBMITTED",
        }

    return run_idempotent(
        session,
        response,
        OperationType.LOAN_APPLICATION,
        payload.external_reference,
        payload.model_dump(),
        action,
    )


@router.post(
    "/{loan_reference}/approve",
    response_model=LoanApproved | InProgressOut,
    status_code=200,
    responses={202: {}, 409: {}},
)
def approve_loan(
    loan_reference: str,
    payload: LoanApproval,
    request: Request,
    response: Response,
    session: SessionDep,
    fineract: FineractDep,
) -> dict[str, Any]:
    """Approve a loan submitted with approve=false. Idempotent on external_reference; a loan that
    is not pending approval answers 409."""
    cid = correlation_id_of(request)

    def action() -> tuple[int, dict[str, Any]]:
        approve(session, fineract, loan_reference, payload.approved_on, cid)
        return 200, {
            "loan_reference": loan_reference,
            "status": "APPROVED",
            "approved_on": payload.approved_on,
        }

    return run_idempotent(
        session,
        response,
        OperationType.LOAN_APPROVAL,
        payload.external_reference,
        {**payload.model_dump(), "loan_reference": loan_reference},
        action,
    )


@router.get("/{loan_reference}", response_model=LoanOut)
def get_loan(
    loan_reference: str, request: Request, session: SessionDep, fineract: FineractDep
) -> dict[str, Any]:
    """Fineract's actual status, balances and transactions for the loan. No Fineract ids."""
    return loan_view(session, fineract, loan_reference, correlation_id_of(request))


def _financial(
    operation: OperationType,
    loan_reference: str,
    reference: str,
    amount: Any,
    on: Any,
    payload: dict[str, Any],
    request: Request,
    response: Response,
    session: Session,
    fineract: FineractLoanGateway,
    erp: ErpGateway,
) -> dict[str, Any]:
    cid = correlation_id_of(request)

    def action() -> tuple[int, dict[str, Any]]:
        tx = execute(session, fineract, erp, operation, reference, loan_reference, amount, on, cid)
        # 202 while the ERP leg is still pending; the recovery sweep completes it
        return (200 if tx.state.value == "COMPLETED" else 202), tx_view(tx)

    def in_progress() -> dict[str, Any]:
        try:
            return {"transaction": tx_view(get_transaction(session, reference))}
        except Exception:
            return {}

    return run_idempotent(session, response, operation, reference, payload, action, in_progress)


@router.post(
    "/{loan_reference}/disburse",
    response_model=TransactionOut | InProgressOut,
    responses={202: {}, 409: {}, 503: {}},
)
def disburse(
    loan_reference: str,
    payload: DisbursementRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    fineract: FineractDep,
    erp: ErpDep,
) -> dict[str, Any]:
    """Disburse once. The amount is the approved principal (single-disbursement product); sending
    `amount` is optional and only checked for consistency. Idempotent on external_reference; one
    disbursement per loan is enforced by a database index, and Fineract's loan state is the last
    line of defence."""
    loan = session.scalar(select(Loan).where(Loan.loan_reference == loan_reference))
    amount = payload.amount if payload.amount is not None else (loan.principal if loan else None)
    return _financial(
        OperationType.DISBURSEMENT,
        loan_reference,
        payload.external_reference,
        amount,
        payload.disbursed_on,
        {**payload.model_dump(), "loan_reference": loan_reference},
        request,
        response,
        session,
        fineract,
        erp,
    )


@router.post(
    "/{loan_reference}/repayments",
    response_model=TransactionOut | InProgressOut,
    responses={202: {}, 409: {}, 503: {}},
)
def repay(
    loan_reference: str,
    payload: RepaymentRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    fineract: FineractDep,
    erp: ErpDep,
) -> dict[str, Any]:
    """Repayment. Idempotent on external_reference; many repayments may hit one loan."""
    return _financial(
        OperationType.REPAYMENT,
        loan_reference,
        payload.external_reference,
        payload.amount,
        payload.paid_on,
        {**payload.model_dump(), "loan_reference": loan_reference},
        request,
        response,
        session,
        fineract,
        erp,
    )
