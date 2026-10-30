"""ORM model. Part A (Python side): one mapping row per ERP GL account with its sync status."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SyncStatus(StrEnum):
    SYNC_PENDING = "SYNC_PENDING"
    SYNCED = "SYNCED"
    FAILED = "FAILED"


class GLAccountMapping(Base):
    """ERP account -> Fineract GL account. external_reference is unique: sending the same account
    any number of times yields one row, and the Fineract id is recorded once."""

    __tablename__ = "gl_account_mappings"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_reference: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    erp_code: Mapped[str] = mapped_column(String(20), nullable=False)
    erp_name: Mapped[str] = mapped_column(String(200), nullable=False)
    erp_type: Mapped[str] = mapped_column(String(20), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    fineract_gl_type: Mapped[int] = mapped_column(Integer, nullable=False)
    fineract_gl_account_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[SyncStatus] = mapped_column(
        Enum(SyncStatus, name="sync_status"), nullable=False, default=SyncStatus.SYNC_PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# --------------------------------------------------------------------------- Parts C, E, F, J


class OperationType(StrEnum):
    CLIENT_CREATE = "CLIENT_CREATE"
    LOAN_APPLICATION = "LOAN_APPLICATION"
    LOAN_APPROVAL = "LOAN_APPROVAL"
    DISBURSEMENT = "DISBURSEMENT"
    REPAYMENT = "REPAYMENT"


class IdempotencyStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class TxState(StrEnum):
    """Lifecycle of one financial operation (Part J). Allowed transitions are enforced in
    app.services.transactions; this enum is the vocabulary."""

    RECEIVED = "RECEIVED"
    FINERACT_PENDING = "FINERACT_PENDING"
    FINERACT_DONE = "FINERACT_DONE"
    ERP_PENDING = "ERP_PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    NEEDS_RECONCILIATION = "NEEDS_RECONCILIATION"


class Client(Base):
    """client_uid -> Fineract client. The Fineract id is stored here and never leaves the
    service."""

    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_uid: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    fineract_client_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Loan(Base):
    """loan_reference (issued here) -> Fineract loan. Fineract loan externalId == loan_reference."""

    __tablename__ = "loans"

    id: Mapped[int] = mapped_column(primary_key=True)
    loan_reference: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    client_uid: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    fineract_loan_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    product_name: Mapped[str] = mapped_column(String(100), nullable=False)
    principal: Mapped[Decimal] = mapped_column(Numeric(19, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdempotencyKey(Base):
    """Part F. One row per (operation type, external_reference): the atomic claim that makes a
    business operation run once. The unique constraint is the mechanism, not application memory."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint(
            "operation_type", "external_reference", name="uq_idempotency_operation_reference"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_type: Mapped[OperationType] = mapped_column(
        Enum(OperationType, name="operation_type"), nullable=False
    )
    external_reference: Mapped[str] = mapped_column(String(100), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[IdempotencyStatus] = mapped_column(
        Enum(IdempotencyStatus, name="idempotency_status"), nullable=False
    )
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LoanTransaction(Base):
    """A disbursement or repayment moving through the Part J state machine."""

    __tablename__ = "loan_transactions"
    __table_args__ = (
        # exactly one live disbursement per loan, enforced by the database (FAILED rows
        # release the slot)
        Index(
            "uq_loan_transactions_one_disbursement_per_loan",
            "loan_reference",
            unique=True,
            postgresql_where=text("operation_type = 'DISBURSEMENT' AND state <> 'FAILED'"),
            sqlite_where=text("operation_type = 'DISBURSEMENT' AND state <> 'FAILED'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    external_reference: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    operation_type: Mapped[OperationType] = mapped_column(
        Enum(OperationType, name="operation_type"), nullable=False
    )
    loan_reference: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 2), nullable=False)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    state: Mapped[TxState] = mapped_column(Enum(TxState, name="tx_state"), nullable=False)
    fineract_transaction_id: Mapped[int | None] = mapped_column(Integer)
    # Fineract's own breakdown (principal/interest/fees/penalties/overpayment), as JSON
    fineract_breakdown: Mapped[str | None] = mapped_column(Text)
    erp_entry_id: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
