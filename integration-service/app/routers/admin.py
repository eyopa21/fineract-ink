"""Operator endpoints: fault injection on outbound calls (Part J)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.clients.erp import ErpGateway
from app.clients.fineract import FineractLoanGateway
from app.db import get_session
from app.deps import get_erp, get_fineract
from app.faults import get_fault_state, set_fault_state
from app.middleware import correlation_id_of
from app.schemas import FaultConfig, RecoveryReport
from app.services.transactions import recover

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/faults", response_model=FaultConfig)
def read_faults() -> FaultConfig:
    return FaultConfig(mode=get_fault_state().mode)


@router.put("/faults", response_model=FaultConfig)
def write_faults(config: FaultConfig) -> FaultConfig:
    return FaultConfig(mode=set_fault_state(config.mode).mode)


@router.post("/recover", response_model=RecoveryReport)
def recover_now(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    fineract: Annotated[FineractLoanGateway, Depends(get_fineract)],
    erp: Annotated[ErpGateway, Depends(get_erp)],
) -> dict[str, Any]:
    """Part J: finish or route every interrupted transaction (also runs at startup)."""
    return recover(session, fineract, erp, correlation_id_of(request))
