"""GL account sync endpoints (Part A, Python side)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.erp import ErpGateway, ErpTimeoutError, ErpUnavailableError
from app.clients.fineract import FineractGateway
from app.db import get_session
from app.deps import get_erp, get_fineract
from app.errors import UpstreamUnavailableError
from app.middleware import correlation_id_of
from app.models import GLAccountMapping
from app.schemas import GLAccountMappingOut, SyncReportOut
from app.services.gl_sync import sync_all

router = APIRouter(prefix="/api/accounts", tags=["gl-sync"])

SessionDep = Annotated[Session, Depends(get_session)]
FineractDep = Annotated[FineractGateway, Depends(get_fineract)]
ErpDep = Annotated[ErpGateway, Depends(get_erp)]


@router.post("/sync", response_model=SyncReportOut)
def sync_accounts(
    request: Request, session: SessionDep, fineract: FineractDep, erp: ErpDep
) -> SyncReportOut:
    """Pull the ERP chart of accounts and create the matching Fineract GL accounts.
    Create-only and idempotent: call it as often as you like. Each account ends SYNCED or FAILED;
    FAILED accounts are retried on the next call. Fineract being down never loses state."""
    try:
        report = sync_all(session, fineract, erp, correlation_id_of(request))
    except ErpUnavailableError as exc:
        raise UpstreamUnavailableError("erp", str(exc)) from exc
    except ErpTimeoutError as exc:
        raise UpstreamUnavailableError("erp", f"timeout: {exc}") from exc
    rows = session.scalars(select(GLAccountMapping).order_by(GLAccountMapping.erp_code)).all()
    return SyncReportOut(
        requested=report.requested,
        created=report.created,
        replayed=report.replayed,
        already_synced=report.already_synced,
        resolved_by_lookup=report.resolved_by_lookup,
        failed=report.failed,
        accounts=[GLAccountMappingOut.model_validate(r) for r in rows],
    )


@router.get("/sync", response_model=list[GLAccountMappingOut])
def sync_status(session: SessionDep) -> list[GLAccountMapping]:
    """Current mapping table: one row per ERP account with its sync status."""
    return list(session.scalars(select(GLAccountMapping).order_by(GLAccountMapping.erp_code)))
