"""Journal entries: balanced, idempotent on external_reference, looked up by reference."""

import json
from decimal import Decimal
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.db import get_session
from app.errors import ConflictError, NotFoundError, UnprocessableError
from app.faults import get_fault_state, sleep_for_fault
from app.idempotency import request_hash
from app.models import Account, Direction, JournalEntry, JournalLine
from app.schemas import JournalEntryCreate, JournalEntryOut, JournalLineOut

router = APIRouter(prefix="/api/journal-entries", tags=["journal-entries"])
log = structlog.get_logger()

SessionDep = Annotated[Session, Depends(get_session)]


def to_out(entry: JournalEntry) -> JournalEntryOut:
    debits = sum((ln.amount for ln in entry.lines if ln.direction is Direction.DEBIT), Decimal(0))
    credits = sum((ln.amount for ln in entry.lines if ln.direction is Direction.CREDIT), Decimal(0))
    return JournalEntryOut(
        entry_id=entry.id,
        external_reference=entry.external_reference,
        entry_date=entry.entry_date,
        description=entry.description,
        currency=entry.currency,
        status="POSTED",
        total_debits=debits,
        total_credits=credits,
        lines=[JournalLineOut.model_validate(line) for line in entry.lines],
        metadata=json.loads(entry.metadata_json) if entry.metadata_json else None,
        created_at=entry.created_at,
    )


def _find(session: Session, reference: str) -> JournalEntry | None:
    return session.scalar(select(JournalEntry).where(JournalEntry.external_reference == reference))


@router.post(
    "",
    response_model=JournalEntryOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"description": "Identical replay: the original entry", "model": JournalEntryOut},
        409: {},
        422: {},
    },
)
async def post_journal_entry(
    payload: JournalEntryCreate, session: SessionDep, response: Response
) -> JournalEntryOut:
    """Post a balanced journal entry. Unbalanced entries are rejected (422). Idempotent on
    external_reference: identical replay returns the original (200), a different payload
    conflicts (409). Posting is one transaction: fully recorded or not at all."""
    digest = request_hash(payload.model_dump())
    log.info(
        "journal entry requested",
        external_reference=payload.external_reference,
        lines=len(payload.lines),
    )

    existing = _find(session, payload.external_reference)
    if existing is None:
        codes = {line.account_code for line in payload.lines}
        known = set(session.scalars(select(Account.code).where(Account.code.in_(codes))))
        missing = sorted(codes - known)
        if missing:
            raise UnprocessableError(
                "unknown account code(s)", [{"field": "lines.account_code", "missing": missing}]
            )
        currencies = set(session.scalars(select(Account.currency).where(Account.code.in_(codes))))
        if currencies != {payload.currency}:
            raise UnprocessableError(
                "entry currency does not match account currency",
                [{"entry": payload.currency, "accounts": sorted(currencies)}],
            )

        entry = JournalEntry(
            external_reference=payload.external_reference,
            entry_date=payload.entry_date,
            description=payload.description,
            currency=payload.currency,
            metadata_json=json.dumps(payload.metadata, sort_keys=True)
            if payload.metadata
            else None,
            request_hash=digest,
            lines=[
                JournalLine(account_code=ln.account_code, direction=ln.direction, amount=ln.amount)
                for ln in payload.lines
            ],
        )
        session.add(entry)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = _find(session, payload.external_reference)
            if existing is None:
                raise
        else:
            session.refresh(entry)
            log.info(
                "journal entry posted",
                external_reference=entry.external_reference,
                erp_transaction_id=entry.id,
                status="POSTED",
            )
            fault = get_fault_state()
            if fault.mode == "timeout_after_write":
                log.warning(
                    "fault injected", mode="timeout_after_write", erp_transaction_id=entry.id
                )
                await run_in_threadpool(sleep_for_fault, fault)
            return to_out(entry)

    if existing.request_hash != digest:
        log.warning(
            "journal entry conflict",
            external_reference=payload.external_reference,
            status="CONFLICT",
        )
        raise ConflictError(
            f"external_reference {payload.external_reference} already exists "
            "with a different payload",
            [{"field": "external_reference", "existing_entry_id": existing.id}],
        )
    log.info(
        "journal entry replayed",
        external_reference=existing.external_reference,
        erp_transaction_id=existing.id,
        status="REPLAYED",
    )
    response.status_code = status.HTTP_200_OK
    response.headers["X-Idempotent-Replay"] = "true"
    return to_out(existing)


@router.get("/{external_reference}", response_model=JournalEntryOut)
def get_journal_entry(external_reference: str, session: SessionDep) -> JournalEntryOut:
    entry = _find(session, external_reference)
    if entry is None:
        raise NotFoundError(f"journal entry {external_reference} not found")
    return to_out(entry)
