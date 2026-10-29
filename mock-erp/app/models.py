"""ORM model: chart of accounts and double-entry journal.

Uniqueness that the assessment requires the ERP to enforce itself lives here as database
constraints:
account code, account external_reference and journal entry external_reference are unique columns.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class AccountType(StrEnum):
    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    EQUITY = "EQUITY"
    INCOME = "INCOME"
    EXPENSE = "EXPENSE"


class Direction(StrEnum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


AMOUNT = Numeric(19, 2)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[AccountType] = mapped_column(
        Enum(AccountType, name="account_type"), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    external_reference: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    # canonical hash of the creation payload; identical replays match it, others conflict
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_reference: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    # free-form caller context (loan_reference, fineract_transaction_id, transaction_type ...)
    metadata_json: Mapped[str | None] = mapped_column(String(2000))
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lines: Mapped[list["JournalLine"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan", order_by="JournalLine.id"
    )


class JournalLine(Base):
    __tablename__ = "journal_lines"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_journal_lines_amount_positive"),
        Index("ix_journal_lines_account_code", "account_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(
        ForeignKey("journal_entries.id", ondelete="CASCADE"), nullable=False
    )
    account_code: Mapped[str] = mapped_column(ForeignKey("accounts.code"), nullable=False)
    direction: Mapped[Direction] = mapped_column(Enum(Direction, name="direction"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)

    entry: Mapped[JournalEntry] = relationship(back_populates="lines")
