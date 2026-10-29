"""Pure accounting rules."""

from decimal import Decimal

from app.accounting import Posting, compute_balance, is_balanced
from app.models import AccountType, Direction

D, C = Direction.DEBIT, Direction.CREDIT


def test_is_balanced() -> None:
    assert is_balanced([Posting(D, Decimal("100.00")), Posting(C, Decimal("100.00"))])
    assert is_balanced(
        [Posting(D, Decimal("60")), Posting(D, Decimal("40")), Posting(C, Decimal("100"))]
    )
    assert not is_balanced([Posting(D, Decimal("100")), Posting(C, Decimal("99.99"))])
    assert is_balanced([])


def test_debit_normal_accounts_grow_with_debits() -> None:
    postings = [Posting(D, Decimal("5000000")), Posting(C, Decimal("400000"))]
    for account_type in (AccountType.ASSET, AccountType.EXPENSE):
        result = compute_balance(account_type, postings)
        assert result.balance == Decimal("4600000")
        assert (result.side, result.amount) == (D, Decimal("4600000"))
        assert (result.debits, result.credits) == (Decimal("5000000"), Decimal("400000"))


def test_credit_normal_accounts_grow_with_credits() -> None:
    postings = [Posting(C, Decimal("75000")), Posting(D, Decimal("5000"))]
    for account_type in (AccountType.INCOME, AccountType.LIABILITY, AccountType.EQUITY):
        assert compute_balance(account_type, postings).balance == Decimal("70000")


def test_empty_balance_is_zero() -> None:
    result = compute_balance(AccountType.ASSET, [])
    assert result.balance == Decimal(0)
    assert result.side is D and result.amount == Decimal(0)


def test_contra_position_is_allowed_and_reported_on_the_other_side() -> None:
    # a refunded fee: debit to an income account with nothing credited yet
    result = compute_balance(AccountType.INCOME, [Posting(D, Decimal("1.00"))])
    assert result.balance == Decimal("-1.00")
    assert (result.side, result.amount) == (D, Decimal("1.00"))
    # an overdrawn bank account
    result = compute_balance(AccountType.ASSET, [Posting(C, Decimal("250.00"))])
    assert result.balance == Decimal("-250.00")
    assert (result.side, result.amount) == (C, Decimal("250.00"))
