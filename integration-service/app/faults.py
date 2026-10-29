"""On-demand failure injection on the Integration Service's outbound calls (Part J).

Modes:
  none                         normal operation
  fineract_down                every Fineract call fails before sending (connection refused)
  fineract_timeout_after_send  the Fineract request is sent and processed, but the response is
                               dropped and a timeout is raised to the caller (scenario 3)
  erp_down                     every ERP call fails before sending
  crash_after_fineract         simulated process crash between Fineract success and the ERP call
                               (scenario 5; used from Part J onwards)
"""

import threading
from dataclasses import dataclass, replace

MODES = ("none", "fineract_down", "fineract_timeout_after_send", "erp_down", "crash_after_fineract")


@dataclass(frozen=True)
class FaultState:
    mode: str = "none"


_lock = threading.Lock()
_state = FaultState()


def get_fault_state() -> FaultState:
    with _lock:
        return _state


def set_fault_state(mode: str) -> FaultState:
    global _state
    if mode not in MODES:
        raise ValueError(f"unknown fault mode {mode!r}")
    with _lock:
        _state = replace(_state, mode=mode)
        return _state
