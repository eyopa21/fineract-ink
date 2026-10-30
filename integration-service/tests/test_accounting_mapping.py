"""Part H: pure mapping from Fineract's breakdown to balanced ERP entries."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.accounting_mapping import FineractBreakdown, MappingError, journal_entry_payload


def lines(payload: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(ln["account_code"], ln["direction"], ln["amount"]) for ln in payload["lines"]]


def test_disbursement_is_dr_receivable_cr_bank() -> None:
    p = journal_entry_payload(
        "DISBURSEMENT",
        "LN-1-DISB",
        "LN-1",
        17,
        date(2026, 1, 15),
        FineractBreakdown(Decimal("5000000")),
    )
    assert lines(p) == [("1200", "DEBIT", "5000000"), ("1100", "CREDIT", "5000000")]
    assert (
        p["external_reference"] == "LN-1-DISB" and p["metadata"]["fineract_transaction_id"] == "17"
    )


def test_repayment_splits_principal_and_interest_from_fineract_breakdown() -> None:
    b = FineractBreakdown(
        amount=Decimal("458333"), principal=Decimal("383333"), interest=Decimal("75000")
    )
    p = journal_entry_payload("REPAYMENT", "LN-1-R1", "LN-1", 18, date(2026, 2, 15), b)
    assert lines(p) == [
        ("1100", "DEBIT", "458333"),
        ("1200", "CREDIT", "383333"),
        ("4000", "CREDIT", "75000"),
    ]


def test_fees_penalties_and_overpayment_get_their_own_credit_lines() -> None:
    b = FineractBreakdown(
        amount=Decimal("1000"),
        principal=Decimal("600"),
        interest=Decimal("200"),
        fees=Decimal("50"),
        penalties=Decimal("50"),
        overpayment=Decimal("100"),
    )
    p = journal_entry_payload("REPAYMENT", "R", "L", 1, date(2026, 3, 1), b)
    assert lines(p) == [
        ("1100", "DEBIT", "1000"),
        ("1200", "CREDIT", "600"),
        ("4000", "CREDIT", "200"),
        ("4100", "CREDIT", "50"),
        ("4200", "CREDIT", "50"),
        ("2100", "CREDIT", "100"),
    ]


def test_breakdown_that_does_not_add_up_is_refused_not_invented() -> None:
    b = FineractBreakdown(amount=Decimal("1000"), principal=Decimal("600"), interest=Decimal("300"))
    with pytest.raises(MappingError, match="does not add up"):
        journal_entry_payload("REPAYMENT", "R", "L", 1, date(2026, 3, 1), b)


def test_reversal_flips_every_direction_and_suffixes_the_reference() -> None:
    b = FineractBreakdown(amount=Decimal("1000"), principal=Decimal("900"), interest=Decimal("100"))
    p = journal_entry_payload("REPAYMENT", "R", "L", 1, date(2026, 3, 1), b, reversal=True)
    assert p["external_reference"] == "R-REV"
    assert lines(p) == [
        ("1100", "CREDIT", "1000"),
        ("1200", "DEBIT", "900"),
        ("4000", "DEBIT", "100"),
    ]
    assert p["metadata"]["reversal"] is True


def test_from_fineract_reads_the_portion_fields() -> None:
    b = FineractBreakdown.from_fineract(
        {
            "amount": 458333,
            "principalPortion": 383333,
            "interestPortion": 75000,
            "feeChargesPortion": 0,
        }
    )
    assert (b.amount, b.principal, b.interest, b.fees, b.penalties) == (
        Decimal("458333"),
        Decimal("383333"),
        Decimal("75000"),
        Decimal("0"),
        Decimal("0"),
    )
