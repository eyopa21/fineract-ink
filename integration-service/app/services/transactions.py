"""Parts E, F, H, I, J: disbursements and repayments through an explicit state machine.

  RECEIVED -> FINERACT_PENDING -> FINERACT_DONE -> ERP_PENDING -> COMPLETED
                    |                                   |
                    v (nothing reached Fineract)        v (ERP conflict / cannot determine)
                  FAILED                          NEEDS_RECONCILIATION

Allowed transitions are the arrows above and nothing else. What is safe to retry:
  - FAILED: nothing happened in Fineract; the caller may re-send (same reference re-claims).
  - FINERACT_PENDING after a crash: never re-send; look the transaction up in Fineract by its
    external id. Found -> continue as FINERACT_DONE; not found -> FAILED.
  - FINERACT_DONE / ERP_PENDING: re-post to the ERP, which is idempotent on the reference.
Retry policy: ERP posting is attempted immediately, then by the recovery sweep (startup sweep or
POST /admin/recover) with a cap of ERP_MAX_ATTEMPTS; beyond that -> NEEDS_RECONCILIATION.
"""

import json
from datetime import date
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.accounting_mapping import FineractBreakdown, MappingError, journal_entry_payload
from app.clients.erp import ErpConflictError, ErpGateway, ErpTimeoutError, ErpUnavailableError
from app.clients.fineract import (
    FineractClientError,
    FineractConflictError,
    FineractLoanGateway,
    FineractTimeoutError,
    FineractUnavailableError,
)
from app.errors import (
    ApiError,
    ConflictError,
    NotFoundError,
    UnprocessableError,
    UpstreamUnavailableError,
)
from app.faults import get_fault_state
from app.idempotency import complete, find, release_stale
from app.models import Loan, LoanTransaction, OperationType, TxState
from app.services.fineract_dates import DATE_FORMAT, LOCALE, to_fineract

log = structlog.get_logger()
ERP_MAX_ATTEMPTS = 10
PAYMENT_TYPE_ID = 1  # "Money Transfer", seeded by Fineract

ALLOWED: dict[TxState, set[TxState]] = {
    TxState.RECEIVED: {TxState.FINERACT_PENDING, TxState.FAILED},
    TxState.FINERACT_PENDING: {TxState.FINERACT_DONE, TxState.FAILED, TxState.NEEDS_RECONCILIATION},
    TxState.FINERACT_DONE: {TxState.ERP_PENDING},
    TxState.ERP_PENDING: {TxState.COMPLETED, TxState.NEEDS_RECONCILIATION},
    TxState.COMPLETED: set(),
    TxState.FAILED: {TxState.RECEIVED},  # a retry with the same reference reuses the row
    TxState.NEEDS_RECONCILIATION: set(),
}


class SimulatedCrashError(RuntimeError):
    """Injected fault: the process dies between Fineract success and the ERP call."""


def transition(
    session: Session, tx: LoanTransaction, to: TxState, error: str | None = None
) -> None:
    if to not in ALLOWED[tx.state]:
        raise RuntimeError(f"illegal transition {tx.state} -> {to} for {tx.external_reference}")
    log.info(
        "transaction state",
        external_reference=tx.external_reference,
        loan_reference=tx.loan_reference,
        status=to,
        previous=tx.state,
    )
    tx.state = to
    tx.last_error = error[:1000] if error else None
    session.commit()


def tx_view(tx: LoanTransaction) -> dict[str, Any]:
    return {
        "external_reference": tx.external_reference,
        "operation": tx.operation_type,
        "loan_reference": tx.loan_reference,
        "amount": tx.amount,
        "transaction_date": tx.transaction_date,
        "state": tx.state,
        "fineract_transaction_id": tx.fineract_transaction_id,
        "fineract_breakdown": json.loads(tx.fineract_breakdown) if tx.fineract_breakdown else None,
        "erp_entry_id": tx.erp_entry_id,
        "attempts": tx.attempts,
        "last_error": tx.last_error,
    }


def get_transaction(session: Session, external_reference: str) -> LoanTransaction:
    tx = session.scalar(
        select(LoanTransaction).where(LoanTransaction.external_reference == external_reference)
    )
    if tx is None:
        raise NotFoundError(f"transaction {external_reference} not found")
    return tx


def _receive(
    session: Session,
    operation: OperationType,
    reference: str,
    loan_reference: str,
    amount: Decimal,
    on: date,
) -> LoanTransaction:
    loan = session.scalar(select(Loan).where(Loan.loan_reference == loan_reference))
    if loan is None:
        raise NotFoundError(f"loan {loan_reference} not found")
    if operation is OperationType.DISBURSEMENT and amount != loan.principal:
        raise UnprocessableError(
            f"disbursement amount {amount} must equal the approved principal {loan.principal} "
            "(single-disbursement product); omit amount to disburse the approved principal",
            [{"field": "amount", "approved_principal": str(loan.principal)}],
        )
    previous = session.scalar(
        select(LoanTransaction).where(LoanTransaction.external_reference == reference)
    )
    if previous is not None:
        if previous.state is not TxState.FAILED:
            raise ConflictError(f"transaction {reference} already exists ({previous.state})")
        # the earlier attempt changed nothing: reuse the row for this retry
        previous.amount = amount
        previous.transaction_date = on
        previous.loan_reference = loan_reference
        transition(session, previous, TxState.RECEIVED)
        return previous
    tx = LoanTransaction(
        external_reference=reference,
        operation_type=operation,
        loan_reference=loan_reference,
        amount=amount,
        transaction_date=on,
        state=TxState.RECEIVED,
    )
    session.add(tx)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        other = session.scalar(
            select(LoanTransaction).where(
                LoanTransaction.loan_reference == loan_reference,
                LoanTransaction.operation_type == OperationType.DISBURSEMENT,
                LoanTransaction.state != TxState.FAILED,
            )
        )
        if operation is OperationType.DISBURSEMENT and other is not None:
            raise ConflictError(
                f"loan {loan_reference} already has a disbursement "
                f"({other.external_reference}, {other.state})",
                [{"field": "loan_reference", "existing_reference": other.external_reference}],
            ) from None
        raise ConflictError(f"transaction {reference} already exists") from None
    return tx


def _fineract_payload(tx: LoanTransaction) -> tuple[str, dict[str, Any]]:
    common = {
        "locale": LOCALE,
        "dateFormat": DATE_FORMAT,
        "externalId": tx.external_reference,
        "paymentTypeId": PAYMENT_TYPE_ID,
    }
    if tx.operation_type is OperationType.DISBURSEMENT:
        return "disburse", {
            **common,
            "actualDisbursementDate": to_fineract(tx.transaction_date),
            "transactionAmount": int(tx.amount),
        }
    return "repayment", {
        **common,
        "transactionDate": to_fineract(tx.transaction_date),
        "transactionAmount": int(tx.amount),
    }


def _record_fineract_done(
    session: Session, fineract: FineractLoanGateway, tx: LoanTransaction, cid: str
) -> None:
    detail = fineract.get_loan_transaction_by_external_ids(
        tx.loan_reference, tx.external_reference, cid
    )
    if detail is None:
        raise RuntimeError(f"Fineract accepted {tx.external_reference} but it cannot be read back")
    tx.fineract_transaction_id = int(detail["id"])
    breakdown = FineractBreakdown.from_fineract(detail)
    tx.fineract_breakdown = json.dumps(breakdown.__dict__, default=str)
    transition(session, tx, TxState.FINERACT_DONE)
    log.info(
        "fineract transaction recorded",
        external_reference=tx.external_reference,
        loan_reference=tx.loan_reference,
        fineract_transaction_id=tx.fineract_transaction_id,
        amount=str(breakdown.amount),
        principal=str(breakdown.principal),
        interest=str(breakdown.interest),
    )


def _settle_idempotency(session: Session, tx: LoanTransaction) -> None:
    """Once a transaction is COMPLETED or NEEDS_RECONCILIATION the Part F key stores the final
    result, so later replays return it with 200 instead of 202."""
    key = find(session, tx.operation_type, tx.external_reference)
    if key is not None:
        complete(session, key, 200, tx_view(tx))


def _post_to_erp(
    session: Session, erp: ErpGateway, tx: LoanTransaction, cid: str
) -> TxState | None:
    """FINERACT_DONE/ERP_PENDING -> COMPLETED. Returns the resulting state, or None when the ERP
    is unreachable and the row stays ERP_PENDING for the recovery sweep."""
    if tx.state is TxState.FINERACT_DONE:
        transition(session, tx, TxState.ERP_PENDING)
    breakdown = FineractBreakdown(
        **{k: Decimal(v) for k, v in json.loads(tx.fineract_breakdown or "{}").items()}
    )
    try:
        payload = journal_entry_payload(
            tx.operation_type,
            tx.external_reference,
            tx.loan_reference,
            tx.fineract_transaction_id,
            tx.transaction_date,
            breakdown,
        )
    except MappingError as exc:
        transition(session, tx, TxState.NEEDS_RECONCILIATION, f"mapping: {exc}")
        _settle_idempotency(session, tx)
        return TxState.NEEDS_RECONCILIATION
    tx.attempts += 1
    try:
        result = erp.post_journal_entry(payload, cid)
    except (ErpUnavailableError, ErpTimeoutError) as exc:
        tx.last_error = f"erp: {exc}"[:1000]
        if tx.attempts >= ERP_MAX_ATTEMPTS:
            transition(
                session,
                tx,
                TxState.NEEDS_RECONCILIATION,
                f"erp unreachable after {tx.attempts} attempts: {exc}",
            )
            _settle_idempotency(session, tx)
            return TxState.NEEDS_RECONCILIATION
        session.commit()
        log.warning(
            "erp posting deferred",
            external_reference=tx.external_reference,
            status=tx.state,
            attempts=tx.attempts,
            error=str(exc),
        )
        return None
    except ErpConflictError as exc:
        transition(session, tx, TxState.NEEDS_RECONCILIATION, f"erp conflict: {exc}")
        _settle_idempotency(session, tx)
        return TxState.NEEDS_RECONCILIATION
    tx.erp_entry_id = result.entry_id
    transition(session, tx, TxState.COMPLETED)
    log.info(
        "transaction completed",
        external_reference=tx.external_reference,
        loan_reference=tx.loan_reference,
        fineract_transaction_id=tx.fineract_transaction_id,
        erp_transaction_id=result.entry_id,
        erp_replayed=result.replayed,
        status=tx.state,
    )
    _settle_idempotency(session, tx)
    return TxState.COMPLETED


def execute(
    session: Session,
    fineract: FineractLoanGateway,
    erp: ErpGateway,
    operation: OperationType,
    reference: str,
    loan_reference: str,
    amount: Decimal,
    on: date,
    cid: str,
) -> LoanTransaction:
    """Run one financial operation end to end. Raises ApiError subclasses for the caller; leaves the
    row in a state the recovery sweep understands when something downstream is unavailable."""
    tx = _receive(session, operation, reference, loan_reference, amount, on)
    transition(session, tx, TxState.FINERACT_PENDING)
    command, payload = _fineract_payload(tx)
    try:
        if operation is OperationType.DISBURSEMENT:
            fineract.loan_command_by_external_id(loan_reference, command, payload, cid)
        else:
            fineract.loan_transaction_by_external_id(loan_reference, command, payload, cid)
    except FineractUnavailableError as exc:
        transition(session, tx, TxState.FAILED, f"fineract unavailable: {exc}")
        raise UpstreamUnavailableError(
            "fineract", "unavailable, nothing was recorded; safe to retry"
        ) from exc
    except FineractTimeoutError as exc:
        # sent, outcome unknown: look it up, never blindly retry
        log.warning(
            "fineract timeout after send, looking up", external_reference=reference, error=str(exc)
        )
        found = fineract.get_loan_transaction_by_external_ids(loan_reference, reference, cid)
        if found is None:
            transition(
                session, tx, TxState.FAILED, f"fineract timeout, transaction not found: {exc}"
            )
            raise UpstreamUnavailableError(
                "fineract", "timeout and no transaction found; safe to retry"
            ) from exc
        log.info(
            "fineract transaction resolved by lookup",
            external_reference=reference,
            fineract_transaction_id=found.get("id"),
        )
    except FineractConflictError as exc:
        transition(session, tx, TxState.FAILED, f"fineract conflict: {exc}")
        raise ConflictError(f"fineract: {exc}") from exc
    except FineractClientError as exc:
        transition(session, tx, TxState.FAILED, f"fineract rejected: {exc}")
        message = exc.body.get("defaultUserMessage") if isinstance(exc.body, dict) else str(exc)
        errors = exc.body.get("errors", []) if isinstance(exc.body, dict) else []
        raise UnprocessableError(
            f"fineract rejected the {operation.lower()}: {message}",
            [{"fineract": e.get("defaultUserMessage")} for e in errors],
        ) from exc

    _record_fineract_done(session, fineract, tx, cid)
    if get_fault_state().mode == "crash_after_fineract":
        log.error(
            "fault injected",
            mode="crash_after_fineract",
            external_reference=reference,
            status=tx.state,
        )
        raise SimulatedCrashError(
            f"crashed after Fineract success for {reference}; state {tx.state} persisted"
        )
    _post_to_erp(session, erp, tx, cid)
    return tx


def recover(
    session: Session, fineract: FineractLoanGateway, erp: ErpGateway, cid: str
) -> dict[str, Any]:
    """Startup sweep / admin endpoint: finish or route every interrupted transaction."""
    pending = session.scalars(
        select(LoanTransaction).where(
            LoanTransaction.state.in_(
                [
                    TxState.RECEIVED,
                    TxState.FINERACT_PENDING,
                    TxState.FINERACT_DONE,
                    TxState.ERP_PENDING,
                ]
            )
        )
    ).all()
    report: dict[str, Any] = {
        "scanned": len(pending),
        "completed": [],
        "failed": [],
        "deferred": [],
        "reconciliation": [],
        "released_keys": release_stale(
            session, [OperationType.CLIENT_CREATE, OperationType.LOAN_APPLICATION]
        ),
    }
    for tx in pending:
        bound = log.bind(external_reference=tx.external_reference, loan_reference=tx.loan_reference)
        try:
            if tx.state in (TxState.RECEIVED, TxState.FINERACT_PENDING):
                if tx.state is TxState.RECEIVED:
                    transition(session, tx, TxState.FINERACT_PENDING)
                found = fineract.get_loan_transaction_by_external_ids(
                    tx.loan_reference, tx.external_reference, cid
                )
                if found is None:
                    transition(
                        session,
                        tx,
                        TxState.FAILED,
                        "recovery: not found in Fineract, nothing was recorded",
                    )
                    report["failed"].append(tx.external_reference)
                    continue
                bound.info("recovery: found in fineract", fineract_transaction_id=found.get("id"))
                _record_fineract_done(session, fineract, tx, cid)
            if tx.state in (TxState.FINERACT_DONE, TxState.ERP_PENDING):
                outcome = _post_to_erp(session, erp, tx, cid)
                if outcome is None:
                    report["deferred"].append(tx.external_reference)
                elif outcome is TxState.COMPLETED:
                    report["completed"].append(tx.external_reference)
                else:
                    report["reconciliation"].append(tx.external_reference)
        except (FineractUnavailableError, FineractTimeoutError) as exc:
            bound.warning("recovery deferred, fineract unreachable", error=str(exc))
            report["deferred"].append(tx.external_reference)
        except ApiError as exc:
            bound.error("recovery error", error=exc.message)
            report["deferred"].append(tx.external_reference)
    log.info(
        "recovery sweep finished",
        **{k: (v if isinstance(v, int) else len(v)) for k, v in report.items()},
    )
    return report
