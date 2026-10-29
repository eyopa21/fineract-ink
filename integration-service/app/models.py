"""ORM model. Part A (Python side): one mapping row per ERP GL account with its sync status."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, Integer, String, func
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
