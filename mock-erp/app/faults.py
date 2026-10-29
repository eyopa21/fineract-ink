"""On-demand failure injection for Part J. Process-local state, controlled via /admin/faults.

Modes:
  none                 normal operation
  down                 every /api request answers 503 immediately
  timeout              every /api request sleeps delay_seconds before answering (callers time out)
  timeout_after_write  POST /api/journal-entries commits the entry, then sleeps (the write happened,
                       the caller never learns it: scenario 4 of Part J)
"""

import threading
import time
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class FaultState:
    mode: str = "none"
    delay_seconds: float = 30.0


_lock = threading.Lock()
_state = FaultState()


def get_fault_state() -> FaultState:
    with _lock:
        return _state


def set_fault_state(mode: str, delay_seconds: float) -> FaultState:
    global _state
    with _lock:
        _state = replace(_state, mode=mode, delay_seconds=delay_seconds)
        return _state


def sleep_for_fault(state: FaultState) -> None:
    time.sleep(state.delay_seconds)
