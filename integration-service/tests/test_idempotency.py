"""Part F decision table, against the database."""

from sqlalchemy.orm import Session

from app.idempotency import Decision, claim, complete, fail, request_hash, stored_response
from app.models import OperationType

OP = OperationType.REPAYMENT


def test_new_then_replay_then_conflict(session: Session) -> None:
    h = request_hash({"amount": "10", "x": 1})
    first = claim(session, OP, "R-1", h)
    assert first.decision is Decision.NEW
    complete(session, first.row, 200, {"ok": True, "amount": "10"})

    again = claim(session, OP, "R-1", h)
    assert again.decision is Decision.REPLAY
    assert stored_response(again.row) == (200, {"ok": True, "amount": "10"})

    other = claim(session, OP, "R-1", request_hash({"amount": "11", "x": 1}))
    assert other.decision is Decision.CONFLICT


def test_in_progress_is_reported_not_rerun(session: Session) -> None:
    h = request_hash({"a": 1})
    assert claim(session, OP, "R-2", h).decision is Decision.NEW
    assert claim(session, OP, "R-2", h).decision is Decision.IN_PROGRESS


def test_failed_is_reclaimed_once_even_with_a_corrected_payload(session: Session) -> None:
    h = request_hash({"a": 1})
    first = claim(session, OP, "R-3", h)
    fail(session, first.row, "fineract rejected: principal too small")
    corrected = request_hash({"a": 2})
    again = claim(session, OP, "R-3", corrected)
    assert again.decision is Decision.NEW
    assert again.row.request_hash == corrected
    assert claim(session, OP, "R-3", corrected).decision is Decision.IN_PROGRESS
    assert claim(session, OP, "R-3", h).decision is Decision.CONFLICT, "old payload now conflicts"


def test_scope_is_per_operation_type(session: Session) -> None:
    h = request_hash({"a": 1})
    assert claim(session, OperationType.DISBURSEMENT, "X", h).decision is Decision.NEW
    assert claim(session, OperationType.REPAYMENT, "X", h).decision is Decision.NEW


def test_hash_ignores_key_order_and_decimal_formatting() -> None:
    from decimal import Decimal

    assert request_hash({"b": Decimal("5.0"), "a": 1}) == request_hash(
        {"a": 1, "b": Decimal("5.00")}
    )


def test_stale_in_progress_application_keys_are_released_by_the_sweep(session: Session) -> None:
    from app.idempotency import release_stale
    from app.models import OperationType as Op

    h = request_hash({"a": 1})
    row = claim(session, Op.LOAN_APPLICATION, "APP-STALE", h).row
    assert release_stale(session, [Op.LOAN_APPLICATION], older_than_s=300) == [], "fresh: kept"
    assert release_stale(session, [Op.LOAN_APPLICATION], older_than_s=0) == [
        "LOAN_APPLICATION:APP-STALE"
    ]
    session.refresh(row)
    assert claim(session, Op.LOAN_APPLICATION, "APP-STALE", h).decision is Decision.NEW
