"""Part F wrapper: claim -> run once -> store the response; replay / 202 / 409 otherwise."""

from collections.abc import Callable
from typing import Any

import structlog
from fastapi import Response
from sqlalchemy.orm import Session

from app.clients.erp import ErpClientError, ErpConflictError, ErpUnavailableError
from app.clients.fineract import (
    FineractClientError,
    FineractConflictError,
    FineractUnavailableError,
)
from app.errors import ApiError, ConflictError
from app.idempotency import Decision, claim, complete, fail, request_hash, stored_response
from app.models import OperationType

log = structlog.get_logger()


def run_idempotent(
    session: Session,
    response: Response,
    operation: OperationType,
    reference: str,
    payload: dict[str, Any],
    action: Callable[[], tuple[int, dict[str, Any]]],
    in_progress_body: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    digest = request_hash(payload)
    decision = claim(session, operation, reference, digest)
    bound = log.bind(external_reference=reference, operation=operation)
    if decision.decision is Decision.CONFLICT:
        bound.warning("idempotency conflict", status="CONFLICT")
        raise ConflictError(
            f"{operation} {reference} was already submitted with a different payload",
            [{"field": "external_reference", "operation": operation}],
        )
    if decision.decision is Decision.REPLAY:
        status_code, body = stored_response(decision.row)
        bound.info("idempotent replay", status="REPLAYED")
        response.status_code = status_code
        response.headers["X-Idempotent-Replay"] = "true"
        return body
    if decision.decision is Decision.IN_PROGRESS:
        bound.info("request still in progress", status="IN_PROGRESS")
        response.status_code = 202
        body = {
            "status": "IN_PROGRESS",
            "operation": operation,
            "external_reference": reference,
            "detail": "a previous request with this reference is still being processed",
        }
        if in_progress_body is not None:
            body.update(in_progress_body())
        return body

    try:
        status_code, body = action()
    except ApiError as exc:
        # the operation changed nothing it can be blamed for: release the key for a retry
        fail(session, decision.row, f"{exc.code}: {exc.message}")
        raise
    except (
        FineractClientError,
        FineractConflictError,
        FineractUnavailableError,
        ErpClientError,
        ErpConflictError,
        ErpUnavailableError,
    ) as exc:
        # rejected or never reached: nothing happened, the reference may be retried (corrected)
        fail(session, decision.row, repr(exc)[:200])
        raise
    except Exception as exc:
        # unknown outcome (e.g. simulated crash): keep the key IN_PROGRESS so replays get 202 until
        # the recovery sweep settles the transaction and completes the key
        bound.error("operation interrupted", error=repr(exc))
        raise
    if status_code == 202:
        # still in progress (e.g. ERP leg pending): the key stays IN_PROGRESS so replays answer 202
        # until the recovery sweep settles the transaction and completes the key
        bound.info("operation accepted, pending downstream", status="IN_PROGRESS")
        response.status_code = 202
        return body
    complete(session, decision.row, status_code, body)
    response.status_code = status_code
    return body
