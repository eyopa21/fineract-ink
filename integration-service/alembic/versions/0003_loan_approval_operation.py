"""loan approval as its own idempotent operation

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-30
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Postgres enums grow in place; ADD VALUE cannot run inside a transaction block
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE operation_type ADD VALUE IF NOT EXISTS 'LOAN_APPROVAL'")


def downgrade() -> None:
    # Postgres cannot drop an enum value; left in place on purpose
    pass
