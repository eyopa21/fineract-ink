"""Part H: Fineract transaction -> ERP journal entry. Pure function, no I/O.

The principal/interest split comes from Fineract's own transaction breakdown (principalPortion,
interestPortion, feeChargesPortion, penaltyChargesPortion, overpaymentPortion), never from a
calculation made here. The entry must balance: the debit side is Fineract's transaction amount and
the credit side is the sum of the portions; if they disagree the mapping refuses rather than invent.

  Disbursement:  DR Loan Receivable (1200) / CR Bank (1100)
  Repayment:     DR Bank (1100) / CR Loan Receivable (1200) principal
                                / CR Interest Income (4000) interest
                                / CR Fee Income (4100) fees, CR Penalty Income (4200) penalties
                                / CR Overpayment Liability (2100) overpayment
  Reversal:      the same lines with every direction flipped, reference suffixed "-REV"
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

BANK = "1100"
LOAN_RECEIVABLE = "1200"
OVERPAYMENT_LIABILITY = "2100"
INTEREST_INCOME = "4000"
FEE_INCOME = "4100"
PENALTY_INCOME = "4200"
CURRENCY = "RWF"

ZERO = Decimal(0)


class MappingError(ValueError):
    pass


@dataclass(frozen=True)
class FineractBreakdown:
    """What Fineract reports for one loan transaction."""

    amount: Decimal
    principal: Decimal = ZERO
    interest: Decimal = ZERO
    fees: Decimal = ZERO
    penalties: Decimal = ZERO
    overpayment: Decimal = ZERO

    @classmethod
    def from_fineract(cls, tx: dict[str, Any]) -> "FineractBreakdown":
        def dec(key: str) -> Decimal:
            value = tx.get(key)
            return Decimal(str(value)) if value is not None else ZERO

        return cls(
            amount=dec("amount"),
            principal=dec("principalPortion"),
            interest=dec("interestPortion"),
            fees=dec("feeChargesPortion"),
            penalties=dec("penaltyChargesPortion"),
            overpayment=dec("overpaymentPortion"),
        )


@dataclass(frozen=True)
class Line:
    account_code: str
    direction: str  # DEBIT | CREDIT
    amount: Decimal


def disbursement_lines(breakdown: FineractBreakdown) -> list[Line]:
    if breakdown.amount <= ZERO:
        raise MappingError("disbursement amount must be positive")
    return [
        Line(LOAN_RECEIVABLE, "DEBIT", breakdown.amount),
        Line(BANK, "CREDIT", breakdown.amount),
    ]


def repayment_lines(breakdown: FineractBreakdown) -> list[Line]:
    if breakdown.amount <= ZERO:
        raise MappingError("repayment amount must be positive")
    portions = [
        (LOAN_RECEIVABLE, breakdown.principal),
        (INTEREST_INCOME, breakdown.interest),
        (FEE_INCOME, breakdown.fees),
        (PENALTY_INCOME, breakdown.penalties),
        (OVERPAYMENT_LIABILITY, breakdown.overpayment),
    ]
    if any(p < ZERO for _, p in portions):
        raise MappingError("negative portion in Fineract breakdown")
    total = sum((p for _, p in portions), ZERO)
    if total != breakdown.amount:
        raise MappingError(
            f"Fineract breakdown {total} does not add up to transaction amount {breakdown.amount}"
        )
    return [Line(BANK, "DEBIT", breakdown.amount)] + [
        Line(code, "CREDIT", p) for code, p in portions if p > ZERO
    ]


def reversed_lines(lines: list[Line]) -> list[Line]:
    flip = {"DEBIT": "CREDIT", "CREDIT": "DEBIT"}
    return [Line(ln.account_code, flip[ln.direction], ln.amount) for ln in lines]


def journal_entry_payload(
    transaction_type: str,
    external_reference: str,
    loan_reference: str,
    fineract_transaction_id: int | None,
    entry_date: date,
    breakdown: FineractBreakdown,
    reversal: bool = False,
) -> dict[str, Any]:
    """The Mock ERP POST /api/journal-entries body for one Fineract transaction."""
    if transaction_type == "DISBURSEMENT":
        lines = disbursement_lines(breakdown)
    elif transaction_type == "REPAYMENT":
        lines = repayment_lines(breakdown)
    else:
        raise MappingError(f"no mapping for transaction type {transaction_type!r}")
    reference = external_reference
    prefix = "Reversal of " if reversal else ""
    if reversal:
        lines = reversed_lines(lines)
        reference = f"{external_reference}-REV"
    return {
        "external_reference": reference,
        "entry_date": entry_date.isoformat(),
        "description": f"{prefix}{transaction_type.title()} {loan_reference}",
        "currency": CURRENCY,
        "lines": [
            {
                "account_code": ln.account_code,
                "direction": ln.direction,
                "amount": format(ln.amount, "f"),
            }
            for ln in lines
        ],
        "metadata": {
            "loan_reference": loan_reference,
            "transaction_type": transaction_type,
            "fineract_transaction_id": str(fineract_transaction_id)
            if fineract_transaction_id
            else None,
            "source_reference": external_reference,
            "reversal": reversal,
        },
    }
