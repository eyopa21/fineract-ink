"""Fineract date conventions: requests take "dd MMMM yyyy" with locale en; responses carry
[y, m, d] arrays."""

from datetime import date
from typing import Any

DATE_FORMAT = "dd MMMM yyyy"
LOCALE = "en"


def to_fineract(value: date) -> str:
    return f"{value.day:02d} {value.strftime('%B')} {value.year}"


def from_fineract(value: Any) -> date | None:
    if isinstance(value, list) and len(value) == 3:
        return date(int(value[0]), int(value[1]), int(value[2]))
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    return None
