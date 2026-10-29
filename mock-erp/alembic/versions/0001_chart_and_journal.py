"""chart of accounts and journal

Revision ID: 0001
Revises:
Create Date: 2026-10-28
"""

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

account_type = sa.Enum("ASSET", "LIABILITY", "EQUITY", "INCOME", "EXPENSE", name="account_type")
direction = sa.Enum("DEBIT", "CREDIT", name="direction")


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("type", account_type, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("external_reference", sa.String(100), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("code", name="uq_accounts_code"),
        sa.UniqueConstraint("external_reference", name="uq_accounts_external_reference"),
    )
    op.create_table(
        "journal_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_reference", sa.String(100), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("metadata_json", sa.String(2000)),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("external_reference", name="uq_journal_entries_external_reference"),
    )
    op.create_table(
        "journal_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "entry_id",
            sa.Integer(),
            sa.ForeignKey("journal_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("account_code", sa.String(20), sa.ForeignKey("accounts.code"), nullable=False),
        sa.Column("direction", direction, nullable=False),
        sa.Column("amount", sa.Numeric(19, 2), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_journal_lines_amount_positive"),
    )
    op.create_index("ix_journal_lines_account_code", "journal_lines", ["account_code"])
    op.create_index("ix_journal_lines_entry_id", "journal_lines", ["entry_id"])


def downgrade() -> None:
    op.drop_table("journal_lines")
    op.drop_table("journal_entries")
    op.drop_table("accounts")
    direction.drop(op.get_bind(), checkfirst=True)
    account_type.drop(op.get_bind(), checkfirst=True)
