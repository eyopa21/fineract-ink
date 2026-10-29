"""Idempotent seeding of the chart of accounts. Safe to run on every start."""

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chart_of_accounts import CURRENCY, SEED_ACCOUNTS, external_reference_for
from app.db import get_session_factory
from app.idempotency import request_hash
from app.models import Account, AccountType

log = structlog.get_logger()


def seed_chart_of_accounts(session: Session) -> int:
    """Insert any seed account whose external_reference is missing. Returns the number inserted."""
    inserted = 0
    for spec in SEED_ACCOUNTS:
        reference = external_reference_for(spec["code"])
        if (
            session.scalar(select(Account.id).where(Account.external_reference == reference))
            is not None
        ):
            continue
        payload = {
            "code": spec["code"],
            "name": spec["name"],
            "type": spec["type"],
            "currency": CURRENCY,
            "external_reference": reference,
        }
        session.add(
            Account(
                code=spec["code"],
                name=spec["name"],
                type=AccountType(spec["type"]),
                currency=CURRENCY,
                external_reference=reference,
                request_hash=request_hash(payload),
            )
        )
        inserted += 1
    session.commit()
    return inserted


def main() -> None:
    from app.config import get_settings
    from app.logging import configure_logging

    configure_logging(get_settings().log_level)
    with get_session_factory()() as session:
        count = seed_chart_of_accounts(session)
    log.info("chart of accounts seeded", inserted=count, total=len(SEED_ACCOUNTS))


if __name__ == "__main__":
    main()
