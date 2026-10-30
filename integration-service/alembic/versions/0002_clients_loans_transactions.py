"""clients, loans, idempotency keys, loan transactions

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-29
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

operation_type = sa.Enum(
    "CLIENT_CREATE", "LOAN_APPLICATION", "DISBURSEMENT", "REPAYMENT", name="operation_type"
)
idempotency_status = sa.Enum("IN_PROGRESS", "COMPLETED", "FAILED", name="idempotency_status")
tx_state = sa.Enum(
    "RECEIVED",
    "FINERACT_PENDING",
    "FINERACT_DONE",
    "ERP_PENDING",
    "COMPLETED",
    "FAILED",
    "NEEDS_RECONCILIATION",
    name="tx_state",
)


def upgrade() -> None:
    op.create_table(
        "clients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("client_uid", sa.String(50), nullable=False),
        sa.Column("fineract_client_id", sa.Integer()),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("client_uid", name="uq_clients_client_uid"),
        sa.UniqueConstraint("fineract_client_id", name="uq_clients_fineract_client_id"),
    )
    op.create_table(
        "loans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("loan_reference", sa.String(50), nullable=False),
        sa.Column("client_uid", sa.String(50), nullable=False),
        sa.Column("fineract_loan_id", sa.Integer()),
        sa.Column("product_name", sa.String(100), nullable=False),
        sa.Column("principal", sa.Numeric(19, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("loan_reference", name="uq_loans_loan_reference"),
        sa.UniqueConstraint("fineract_loan_id", name="uq_loans_fineract_loan_id"),
    )
    op.create_index("ix_loans_client_uid", "loans", ["client_uid"])
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("operation_type", operation_type, nullable=False),
        sa.Column("external_reference", sa.String(100), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", idempotency_status, nullable=False),
        sa.Column("response_status", sa.Integer()),
        sa.Column("response_body", sa.Text()),
        sa.Column("last_error", sa.String(1000)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "operation_type", "external_reference", name="uq_idempotency_operation_reference"
        ),
    )
    op.create_table(
        "loan_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_reference", sa.String(100), nullable=False),
        sa.Column("operation_type", operation_type, nullable=False),
        sa.Column("loan_reference", sa.String(50), nullable=False),
        sa.Column("amount", sa.Numeric(19, 2), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("state", tx_state, nullable=False),
        sa.Column("fineract_transaction_id", sa.Integer()),
        sa.Column("fineract_breakdown", sa.Text()),
        sa.Column("erp_entry_id", sa.Integer()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(1000)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("external_reference", name="uq_loan_transactions_external_reference"),
    )
    op.create_index("ix_loan_transactions_loan_reference", "loan_transactions", ["loan_reference"])
    op.create_index(
        "uq_loan_transactions_one_disbursement_per_loan",
        "loan_transactions",
        ["loan_reference"],
        unique=True,
        postgresql_where=sa.text("operation_type = 'DISBURSEMENT' AND state <> 'FAILED'"),
    )


def downgrade() -> None:
    op.drop_table("loan_transactions")
    op.drop_table("idempotency_keys")
    op.drop_table("loans")
    op.drop_table("clients")
    for enum in (tx_state, idempotency_status, operation_type):
        enum.drop(op.get_bind(), checkfirst=True)
