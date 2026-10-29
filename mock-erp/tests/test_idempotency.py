"""Canonical request hashing."""

from datetime import date
from decimal import Decimal

from app.idempotency import request_hash
from app.models import Direction


def test_key_order_and_decimal_formatting_do_not_change_the_hash() -> None:
    a = {
        "code": "1200",
        "name": "Loan Receivable",
        "amount": Decimal("5000000"),
        "when": date(2026, 1, 15),
    }
    b = {
        "when": date(2026, 1, 15),
        "amount": Decimal("5000000.00"),
        "name": "Loan Receivable",
        "code": "1200",
    }
    assert request_hash(a) == request_hash(b)


def test_any_value_change_changes_the_hash() -> None:
    base = {"code": "1200", "lines": [{"direction": Direction.DEBIT, "amount": Decimal("1.00")}]}
    other = {"code": "1200", "lines": [{"direction": Direction.CREDIT, "amount": Decimal("1.00")}]}
    assert request_hash(base) != request_hash(other)
    assert request_hash(base) != request_hash({**base, "code": "1201"})


def test_enums_hash_as_their_values() -> None:
    assert request_hash({"d": Direction.DEBIT}) == request_hash({"d": "DEBIT"})
