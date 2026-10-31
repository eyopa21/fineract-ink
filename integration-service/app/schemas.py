"""API response models for the GL sync."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


# --------------------------------------------------------------------------- Parts C, E, F

from datetime import date  # noqa: E402
from decimal import Decimal  # noqa: E402
from typing import Annotated, Any  # noqa: E402

from pydantic import StringConstraints  # noqa: E402

from app.models import OperationType, TxState  # noqa: E402

Reference = Annotated[
    str, StringConstraints(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")
]
ClientUid = Annotated[
    str, StringConstraints(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9._-]+$")
]
Money = Annotated[Decimal, Field(gt=0, max_digits=19, decimal_places=2)]


class ClientCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    external_reference: Reference
    client_uid: ClientUid
    full_name: Annotated[str, StringConstraints(min_length=2, max_length=200)]
    activation_date: date


class ClientOut(BaseModel):
    client_uid: str
    full_name: str
    status: str
    active: bool
    activation_date: date | None
    office: str | None


class LoanApplication(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    external_reference: Reference
    client_uid: ClientUid
    principal: Money
    submitted_on: date
    expected_disbursement_date: date
    approve: bool = True


class LoanApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    external_reference: Reference
    approved_on: date


class LoanApproved(BaseModel):
    loan_reference: str
    status: str
    approved_on: date


class LoanCreated(BaseModel):
    loan_reference: str
    client_uid: str
    principal: Decimal
    status: str


class LoanStatus(BaseModel):
    code: str | None
    value: str | None
    active: bool | None
    closed_obligations_met: bool | None


class LoanBalances(BaseModel):
    principal_disbursed: Decimal | None
    principal_paid: Decimal | None
    principal_outstanding: Decimal | None
    interest_charged: Decimal | None
    interest_paid: Decimal | None
    interest_outstanding: Decimal | None
    total_expected_repayment: Decimal | None
    total_repayment: Decimal | None
    total_outstanding: Decimal | None


class LoanTransactionView(BaseModel):
    external_reference: str | None
    type: str | None
    date: date | None
    amount: Decimal | None
    principal: Decimal | None
    interest: Decimal | None
    fees: Decimal | None
    penalties: Decimal | None
    outstanding_after: Decimal | None
    reversed: bool


class LoanOut(BaseModel):
    loan_reference: str
    client_uid: str
    product: str
    principal: Decimal
    status: LoanStatus
    balances: LoanBalances
    transactions: list[LoanTransactionView]


class DisbursementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    external_reference: Reference
    disbursed_on: date
    # Optional: the product is single-disbursement, so the amount is the approved principal. When
    # sent it must equal the approved principal (a consistency check), otherwise 422.
    amount: Money | None = None


class RepaymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    external_reference: Reference
    amount: Money
    paid_on: date


class TransactionOut(BaseModel):
    external_reference: str
    operation: OperationType
    loan_reference: str
    amount: Decimal
    transaction_date: date
    state: TxState
    fineract_transaction_id: int | None
    fineract_breakdown: dict[str, Any] | None
    erp_entry_id: int | None
    attempts: int
    last_error: str | None


class InProgressOut(BaseModel):
    status: str = "IN_PROGRESS"
    operation: OperationType
    external_reference: str
    detail: str
    transaction: TransactionOut | None = None


class ReconciliationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    loan_references: list[str] = []
    external_references: list[str] = []
    date_from: date | None = None
    date_to: date | None = None
    # read-only unless explicitly asked; repair re-posts MISSING_IN_ERP items (idempotent)
    repair: bool = False

    @model_validator(mode="after")
    def some_scope(self) -> "ReconciliationRequest":
        if not (self.loan_references or self.external_references or self.date_from or self.date_to):
            raise ValueError("give loan_references, external_references, or a date range")
        return self


class ReconciliationItem(BaseModel):
    external_reference: str
    loan_reference: str | None
    status: str
    fineract: dict[str, Any] | None
    erp: dict[str, Any] | None
    note: str | None
    integration_state: str | None
    repair: dict[str, Any] | None


class ReconciliationReport(BaseModel):
    scope: dict[str, Any]
    repair: bool
    summary: dict[str, int]
    items: list[ReconciliationItem]


class GlReconciliationItem(BaseModel):
    key: str
    status: str
    erp: dict[str, Any] | None
    fineract: dict[str, Any] | None
    mapping_state: str | None
    note: str | None


class GlReconciliationReport(BaseModel):
    summary: dict[str, int]
    items: list[GlReconciliationItem]


class RecoveryReport(BaseModel):
    scanned: int
    completed: list[str]
    failed: list[str]
    deferred: list[str]
    reconciliation: list[str]
    released_keys: list[str] = []
