"""Part K: POST /api/reconciliation."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.clients.erp import ErpGateway
from app.clients.fineract import FineractLoanGateway
from app.db import get_session
from app.deps import get_erp, get_fineract
from app.middleware import correlation_id_of
from app.schemas import ReconciliationReport, ReconciliationRequest
from app.services.reconciliation import Scope, reconcile

router = APIRouter(prefix="/api/reconciliation", tags=["reconciliation"])


@router.post("", response_model=ReconciliationReport)
def run_reconciliation(
    payload: ReconciliationRequest,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    fineract: Annotated[FineractLoanGateway, Depends(get_fineract)],
    erp: Annotated[ErpGateway, Depends(get_erp)],
) -> dict[str, Any]:
    """Compare Fineract loan transactions with ERP journal entries by external_reference.
    Read-only by default; `repair: true` re-posts entries the ERP is missing, idempotently."""
    scope = Scope(
        payload.loan_references, payload.external_references, payload.date_from, payload.date_to
    )
    return reconcile(session, fineract, erp, scope, payload.repair, correlation_id_of(request))
