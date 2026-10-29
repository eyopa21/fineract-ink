"""API request and response models (Pydantic)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import AccountType, Direction

Amount = Annotated[Decimal, Field(gt=0, max_digits=19, decimal_places=2)]
Code = Annotated[str, Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9._-]+$")]
Reference = Annotated[str, Field(min_length=1, max_length=100)]
Currency = Annotated[str, Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")]


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: Code
    name: Annotated[str, Field(min_length=1, max_length=200)]
    type: AccountType
    currency: Currency
    external_reference: Reference


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    type: AccountType
    currency: str
    external_reference: str
    created_at: datetime


class BalanceOut(BaseModel):
    code: str
    type: AccountType
    currency: str
    debits: Decimal
    credits: Decimal
    # signed, in the account's normal sign (negative = contra position, e.g. a refunded fee)
    balance: Decimal
    # the same balance as a ledger prints it: absolute amount plus DEBIT/CREDIT side
    balance_side: Direction
    balance_amount: Decimal
    entry_count: int


class JournalLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    account_code: Code
    direction: Direction
    amount: Amount


class JournalEntryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    external_reference: Reference
    entry_date: date
    description: Annotated[str, Field(min_length=1, max_length=500)]
    currency: Currency
    lines: Annotated[list[JournalLineIn], Field(min_length=2)]
    metadata: dict[str, Any] | None = None

    @field_validator("metadata")
    @classmethod
    def metadata_is_flat(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        for key, item in value.items():
            if not isinstance(item, str | int | float | bool) and item is not None:
                raise ValueError(f"metadata.{key} must be a scalar")
        return value

    @model_validator(mode="after")
    def must_balance(self) -> "JournalEntryCreate":
        debits = sum(
            (line.amount for line in self.lines if line.direction is Direction.DEBIT), Decimal(0)
        )
        credits = sum(
            (line.amount for line in self.lines if line.direction is Direction.CREDIT), Decimal(0)
        )
        if debits != credits:
            raise ValueError(f"entry does not balance: debits {debits} != credits {credits}")
        if not any(line.direction is Direction.DEBIT for line in self.lines):
            raise ValueError("entry needs at least one debit line")
        return self


class JournalLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_code: str
    direction: Direction
    amount: Decimal


class JournalEntryOut(BaseModel):
    entry_id: int
    external_reference: str
    entry_date: date
    description: str
    currency: str
    status: str
    total_debits: Decimal
    total_credits: Decimal
    lines: list[JournalLineOut]
    metadata: dict[str, Any] | None
    created_at: datetime


class FaultConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = Field(pattern=r"^(none|down|timeout|timeout_after_write)$")
    delay_seconds: Annotated[float, Field(ge=0, le=120)] = 30.0


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[dict[str, Any]] = []


class ErrorOut(BaseModel):
    error: ErrorBody
    correlation_id: str
