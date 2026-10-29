"""Sync service: status transitions, four-times rule, Fineract down, timeout resolved by lookup."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import GLAccountMapping, SyncStatus
from app.services.gl_sync import sync_all
from tests.conftest import SEED, FakeErp, FakeFineract


def rows(session: Session) -> list[GLAccountMapping]:
    return list(session.scalars(select(GLAccountMapping).order_by(GLAccountMapping.erp_code)))


def test_first_sync_creates_everything(
    session: Session, fineract: FakeFineract, erp: FakeErp
) -> None:
    report = sync_all(session, fineract, erp, "c1")
    assert (report.requested, report.created, report.failed) == (3, 3, 0)
    assert [r.status for r in rows(session)] == [SyncStatus.SYNCED] * 3
    assert [r.fineract_gl_account_id for r in rows(session)] == [1, 2, 3]
    assert len(fineract.calls) == 3


def test_sending_the_same_accounts_four_times_yields_one_of_everything(
    session: Session, fineract: FakeFineract, erp: FakeErp
) -> None:
    for _ in range(4):
        sync_all(session, fineract, erp, "c")
    assert len(rows(session)) == 3, "one mapping row per account"
    assert len(fineract.accounts) == 3, "one Fineract GL account per reference"
    assert len(fineract.calls) == 3, "SYNCED rows are never re-sent"
    assert len(erp.accounts) == 3


def test_fineract_down_leaves_failed_rows_that_the_next_sync_repairs(
    session: Session, fineract: FakeFineract, erp: FakeErp
) -> None:
    erp.accounts = SEED[:1]
    fineract.script = ["down", "down", "down"]  # all 3 attempts fail
    report = sync_all(session, fineract, erp, "c")
    row = rows(session)[0]
    assert report.failed == 1
    assert row.status is SyncStatus.FAILED
    assert row.attempts == 3
    assert "unavailable" in (row.last_error or "")
    assert row.fineract_gl_account_id is None

    report = sync_all(session, fineract, erp, "c")  # Fineract is back
    row = rows(session)[0]
    assert report.created == 1
    assert row.status is SyncStatus.SYNCED
    assert row.fineract_gl_account_id == 1
    assert row.last_error is None


def test_transient_failure_is_retried_within_one_sync(
    session: Session, fineract: FakeFineract, erp: FakeErp
) -> None:
    erp.accounts = SEED[:1]
    fineract.script = ["down", "ok"]
    report = sync_all(session, fineract, erp, "c")
    assert report.created == 1 and report.failed == 0
    assert rows(session)[0].attempts == 2


def test_timeout_after_send_is_resolved_by_lookup_not_resend(
    session: Session, fineract: FakeFineract, erp: FakeErp
) -> None:
    erp.accounts = SEED[:1]
    fineract.script = ["timeout_after_send"]
    report = sync_all(session, fineract, erp, "c")
    row = rows(session)[0]
    assert report.resolved_by_lookup == 1
    assert row.status is SyncStatus.SYNCED
    assert row.fineract_gl_account_id == 1
    assert len(fineract.calls) == 1, "no second POST"
    assert len(fineract.accounts) == 1


def test_replay_against_fineract_is_recorded_not_duplicated(
    session: Session, fineract: FakeFineract, erp: FakeErp
) -> None:
    # Fineract already holds the account (e.g. mapping table was lost); Fineract replays it
    fineract.create_gl_account(
        {
            "name": "Bank",
            "glCode": "1100",
            "type": 1,
            "usage": 1,
            "manualEntriesAllowed": True,
            "description": "ERP account 1100 (GL-1100)",
            "externalId": "GL-1100",
        },
        "pre",
    )
    erp.accounts = SEED[:1]
    report = sync_all(session, fineract, erp, "c")
    assert (report.created, report.replayed) == (0, 1)
    assert rows(session)[0].fineract_gl_account_id == 1


def test_conflicting_payload_in_fineract_fails_permanently(
    session: Session, fineract: FakeFineract, erp: FakeErp
) -> None:
    fineract.accounts["GL-1100"] = (99, {"name": "Different", "externalId": "GL-1100"})
    erp.accounts = SEED[:1]
    report = sync_all(session, fineract, erp, "c")
    row = rows(session)[0]
    assert report.failed == 1
    assert row.status is SyncStatus.FAILED
    assert row.attempts == 1, "no retry on a permanent error"
    assert "different payload" in (row.last_error or "")
