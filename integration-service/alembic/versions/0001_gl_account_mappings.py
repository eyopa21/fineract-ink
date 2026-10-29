"""gl account mappings

Revision ID: 0001
Revises:
Create Date: 2026-10-29
"""

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

sync_status = sa.Enum("SYNC_PENDING", "SYNCED", "FAILED", name="sync_status")


def upgrade() -> None:
    op.create_table(
        "gl_account_mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_reference", sa.String(100), nullable=False),
        sa.Column("erp_code", sa.String(20), nullable=False),
        sa.Column("erp_name", sa.String(200), nullable=False),
        sa.Column("erp_type", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("fineract_gl_type", sa.Integer(), nullable=False),
        sa.Column("fineract_gl_account_id", sa.Integer()),
        sa.Column("status", sync_status, nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(1000)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("external_reference", name="uq_gl_account_mappings_external_reference"),
    )


def downgrade() -> None:
    op.drop_table("gl_account_mappings")
    sync_status.drop(op.get_bind(), checkfirst=True)
