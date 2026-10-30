"""Part F: per-operation idempotency, held in the database.

Decision table for (operation type, external_reference):
  no row                          -> NEW        claim the row (INSERT; the unique constraint is
                                                the lock), run once
  row, different request hash     -> CONFLICT   409
  row COMPLETED, same hash        -> REPLAY     return the stored response (same status, same body)
  row IN_PROGRESS, same hash      -> IN_PROGRESS 202 with current status; never a second run
  row FAILED, any hash            -> NEW        the previous attempt changed nothing (rejected or
                                                unreachable); re-claim atomically, with the new
                                                payload's hash, so a corrected request may reuse
                                                the reference

The claim is the INSERT. Two concurrent callers with the same reference race on the unique
constraint: exactly one INSERT succeeds; the loser re-reads the row and lands in IN_PROGRESS or
REPLAY.
"""

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import IdempotencyKey, IdempotencyStatus, OperationType


def _normalise(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value.quantize(Decimal("0.01")), "f")
    if isinstance(value, dict):
        return {str(k): _normalise(v) for k, v in sorted(value.items())}
    if isinstance(value, list | tuple):
        return [_normalise(v) for v in value]
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def request_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(_normalise(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class Decision(StrEnum):
    NEW = "NEW"
    REPLAY = "REPLAY"
    IN_PROGRESS = "IN_PROGRESS"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class Claim:
    decision: Decision
    row: IdempotencyKey


def _get(session: Session, operation: OperationType, reference: str) -> IdempotencyKey | None:
    return session.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.operation_type == operation,
            IdempotencyKey.external_reference == reference,
        )
    )


def find(session: Session, operation: OperationType, reference: str) -> IdempotencyKey | None:
    return _get(session, operation, reference)


def claim(session: Session, operation: OperationType, reference: str, digest: str) -> Claim:
    row = _get(session, operation, reference)
    if row is None:
        row = IdempotencyKey(
            operation_type=operation,
            external_reference=reference,
            request_hash=digest,
            status=IdempotencyStatus.IN_PROGRESS,
        )
        session.add(row)
        try:
            session.commit()
            return Claim(Decision.NEW, row)
        except IntegrityError:
            session.rollback()
            found = _get(session, operation, reference)
            if found is None:  # pragma: no cover - cannot happen after a unique violation
                raise
            row = found

    if row.status is not IdempotencyStatus.FAILED and row.request_hash != digest:
        return Claim(Decision.CONFLICT, row)
    if row.status is IdempotencyStatus.COMPLETED:
        return Claim(Decision.REPLAY, row)
    if row.status is IdempotencyStatus.IN_PROGRESS:
        return Claim(Decision.IN_PROGRESS, row)

    # FAILED: re-claim atomically so two retriers cannot both run
    result = cast(
        CursorResult[Any],
        session.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.id == row.id, IdempotencyKey.status == IdempotencyStatus.FAILED)
            .values(status=IdempotencyStatus.IN_PROGRESS, last_error=None, request_hash=digest)
        ),
    )
    session.commit()
    session.refresh(row)
    if result.rowcount == 1:
        return Claim(Decision.NEW, row)
    return Claim(Decision.IN_PROGRESS, row)


def release_stale(
    session: Session, operations: list[OperationType], older_than_s: int = 300
) -> list[str]:
    """Keys left IN_PROGRESS by a process that died mid-request. Only for operations whose
    outcome the recovery sweep cannot settle through a transaction row (client creation, loan
    application): the service itself re-checks Fineract by external id on the retry."""
    from datetime import UTC, datetime, timedelta

    cutoff = datetime.now(UTC) - timedelta(seconds=older_than_s)
    stale = session.scalars(
        select(IdempotencyKey).where(
            IdempotencyKey.operation_type.in_(operations),
            IdempotencyKey.status == IdempotencyStatus.IN_PROGRESS,
        )
    ).all()
    released: list[str] = []
    for row in stale:
        updated = row.updated_at or row.created_at
        if updated is not None and updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        if updated is not None and updated > cutoff:
            continue
        row.status = IdempotencyStatus.FAILED
        row.last_error = "released by recovery sweep: left IN_PROGRESS by an interrupted request"
        released.append(f"{row.operation_type}:{row.external_reference}")
    session.commit()
    return released


def complete(session: Session, row: IdempotencyKey, status_code: int, body: dict[str, Any]) -> None:
    row.status = IdempotencyStatus.COMPLETED
    row.response_status = status_code
    row.response_body = json.dumps(body, default=str)
    row.last_error = None
    session.commit()


def fail(session: Session, row: IdempotencyKey, error: str) -> None:
    row.status = IdempotencyStatus.FAILED
    row.last_error = error[:1000]
    session.commit()


def stored_response(row: IdempotencyKey) -> tuple[int, dict[str, Any]]:
    body: dict[str, Any] = json.loads(row.response_body or "{}")
    return int(row.response_status or 200), body
