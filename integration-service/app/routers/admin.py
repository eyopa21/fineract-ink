"""Operator endpoints: fault injection on outbound calls (Part J)."""

from fastapi import APIRouter

from app.faults import get_fault_state, set_fault_state
from app.schemas import FaultConfig

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/faults", response_model=FaultConfig)
def read_faults() -> FaultConfig:
    return FaultConfig(mode=get_fault_state().mode)


@router.put("/faults", response_model=FaultConfig)
def write_faults(config: FaultConfig) -> FaultConfig:
    return FaultConfig(mode=set_fault_state(config.mode).mode)
