"""Pure accounting rules: balance sign conventions and balance computation. No I/O."""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from app.models import AccountType, Direction

DEBIT_NORMAL: frozenset[AccountType] = frozenset({AccountType.ASSET, AccountType.EXPENSE})
CREDIT_NORMAL: frozenset[AccountType] = frozenset(
    {AccountType.LIABILITY, AccountType.EQUITY, AccountType.INCOME}
)


@dataclass(frozen=True)
class Posting:
    direction: Direction
    amount: Decimal


@dataclass(frozen=True)
class Balance:
    debits: Decimal
    credits: Decimal
    balance: Decimal
    # side the net balance sits on, as a ledger prints it ("1.00 DR" / "1.00 CR")
    side: Direction
    amount: Decimal


def is_balanced(postings: Iterable[Posting]) -> bool:
    debits = Decimal(0)
    credits = Decimal(0)
    for posting in postings:
        if posting.direction is Direction.DEBIT:
            debits += posting.amount
        else:
            credits += posting.amount
    return debits == credits


def compute_balance(account_type: AccountType, postings: Iterable[Posting]) -> Balance:
    """Balance in the account's normal sign: positive for a debit-normal account with net debits,
    positive for a credit-normal account with net credits. A negative value is legitimate
    (reversals, refunds, overdrafts, contra accounts); `side` and `amount` present it the way a
    ledger does."""
    debits = Decimal(0)
    credits = Decimal(0)
    for posting in postings:
        if posting.direction is Direction.DEBIT:
            debits += posting.amount
        else:
            credits += posting.amount
    net = debits - credits if account_type in DEBIT_NORMAL else credits - debits
    side = Direction.DEBIT if debits >= credits else Direction.CREDIT
    return Balance(debits=debits, credits=credits, balance=net, side=side, amount=abs(net))
