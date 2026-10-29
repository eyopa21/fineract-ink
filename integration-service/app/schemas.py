"""API response models for the GL sync."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import SyncStatus


class GLAccountMappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    external_reference: str
    erp_code: str
    erp_name: str
    erp_type: str
    fineract_gl_type: int
    fineract_gl_account_id: int | None
    status: SyncStatus
    attempts: int
    last_error: str | None
    updated_at: datetime | None = None


class SyncReportOut(BaseModel):
    requested: int
    created: int
    replayed: int
    already_synced: int
    resolved_by_lookup: int
    failed: int
    accounts: list[GLAccountMappingOut]


class FaultConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = Field(
        pattern=r"^(none|fineract_down|fineract_timeout_after_send|erp_down|crash_after_fineract)$"
    )
