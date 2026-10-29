"""Operator endpoints: fault injection for the Part J simulations."""

from fastapi import APIRouter

from app.faults import FaultState, get_fault_state, set_fault_state
from app.schemas import FaultConfig

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/faults", response_model=FaultConfig)
def read_faults() -> FaultConfig:
    state = get_fault_state()
    return FaultConfig(mode=state.mode, delay_seconds=state.delay_seconds)


@router.put("/faults", response_model=FaultConfig)
def write_faults(config: FaultConfig) -> FaultConfig:
    state: FaultState = set_fault_state(config.mode, config.delay_seconds)
    return FaultConfig(mode=state.mode, delay_seconds=state.delay_seconds)
