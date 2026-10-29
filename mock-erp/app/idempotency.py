"""Canonical request hashing: decides replay (same payload) versus conflict (different payload)."""

import hashlib
import json
from decimal import Decimal
from typing import Any


def _normalise(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value.quantize(Decimal("0.01")), "f")
    if isinstance(value, dict):
        return {str(k): _normalise(v) for k, v in sorted(value.items())}
    if isinstance(value, list | tuple):
        return [_normalise(v) for v in value]
    if hasattr(value, "value"):  # enums
        return value.value
    if hasattr(value, "isoformat"):  # dates
        return value.isoformat()
    return value


def request_hash(payload: dict[str, Any]) -> str:
    """SHA-256 of the payload with sorted keys, normalised decimals/dates/enums.
    Two requests that mean the same thing hash the same even if the JSON formatting differs."""
    canonical = json.dumps(_normalise(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
