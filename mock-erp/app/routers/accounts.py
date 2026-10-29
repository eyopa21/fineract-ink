"""Chart of accounts: idempotent creation on external_reference, listing, balances."""

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.accounting import Posting, compute_balance
from app.db import get_session
from app.errors import ConflictError, NotFoundError
from app.idempotency import request_hash
from app.models import Account, JournalLine
from app.schemas import AccountCreate, AccountOut, BalanceOut

router = APIRouter(prefix="/api/accounts", tags=["accounts"])
log = structlog.get_logger()

SessionDep = Annotated[Session, Depends(get_session)]


def _find_by_reference(session: Session, reference: str) -> Account | None:
    return session.scalar(select(Account).where(Account.external_reference == reference))


@router.post(
    "",
    response_model=AccountOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"description": "Identical replay: the existing account", "model": AccountOut},
        409: {},
    },
)
def create_account(payload: AccountCreate, session: SessionDep, response: Response) -> Account:
    """Create an account. Idempotent on external_reference: an identical payload returns the
    existing account with 200, a different payload for the same reference is rejected with 409.
    Uniqueness of code and external_reference is enforced by the database, not by the caller."""
    digest = request_hash(payload.model_dump())
    log.info(
        "account create requested", external_reference=payload.external_reference, code=payload.code
    )

    existing = _find_by_reference(session, payload.external_reference)
    if existing is None:
        account = Account(
            code=payload.code,
            name=payload.name,
            type=payload.type,
            currency=payload.currency,
            external_reference=payload.external_reference,
            request_hash=digest,
        )
        session.add(account)
        try:
            session.commit()
        except IntegrityError:
            # lost a race on external_reference, or the code is taken by another reference
            session.rollback()
            existing = _find_by_reference(session, payload.external_reference)
            if existing is None:
                raise ConflictError(
                    f"account code {payload.code} already exists "
                    "under a different external_reference",
                    [{"field": "code", "value": payload.code}],
                ) from None
        else:
            session.refresh(account)
            log.info(
                "account created", external_reference=account.external_reference, status="CREATED"
            )
            return account

    if existing.request_hash != digest:
        log.warning(
            "account conflict", external_reference=payload.external_reference, status="CONFLICT"
        )
        raise ConflictError(
            f"external_reference {payload.external_reference} already exists "
            "with a different payload",
            [{"field": "external_reference", "existing_code": existing.code}],
        )
    log.info(
        "account create replayed", external_reference=existing.external_reference, status="REPLAYED"
    )
    response.status_code = status.HTTP_200_OK
    response.headers["X-Idempotent-Replay"] = "true"
    return existing


@router.get("", response_model=list[AccountOut])
def list_accounts(session: SessionDep) -> list[Account]:
    return list(session.scalars(select(Account).order_by(Account.code)))


@router.get("/{code}/balance", response_model=BalanceOut)
def account_balance(code: str, session: SessionDep) -> BalanceOut:
    """Balance derived from journal lines at read time. Nothing is stored, so the balance can
    never disagree with the entries."""
    account = session.scalar(select(Account).where(Account.code == code))
    if account is None:
        raise NotFoundError(f"account {code} not found")
    lines = session.scalars(select(JournalLine).where(JournalLine.account_code == code)).all()
    result = compute_balance(account.type, [Posting(line.direction, line.amount) for line in lines])
    return BalanceOut(
        code=account.code,
        type=account.type,
        currency=account.currency,
        debits=result.debits,
        credits=result.credits,
        balance=result.balance,
        balance_side=result.side,
        balance_amount=result.amount,
        entry_count=len({line.entry_id for line in lines}),
    )
