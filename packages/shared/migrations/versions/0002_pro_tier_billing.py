"""pro-tier billing — subscriptions.pro_expires_at/registered_wallet, pro_payments

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-23

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("subscriptions", sa.Column("pro_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("subscriptions", sa.Column("registered_wallet", sa.String, nullable=True))

    op.create_table(
        "pro_payments",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("chain", sa.String, nullable=False),
        sa.Column("tx_hash", sa.String, nullable=False),
        sa.Column("log_index", sa.Integer, nullable=False),
        sa.Column("from_address", sa.String, nullable=False),
        sa.Column("amount_usdc", sa.Float, nullable=False),
        sa.Column("matched", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("credited_channel", sa.String, nullable=True),
        sa.Column("credited_recipient_id", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("chain", "tx_hash", "log_index", name="uq_pro_payment_tx_log"),
    )


def downgrade() -> None:
    op.drop_table("pro_payments")
    op.drop_column("subscriptions", "registered_wallet")
    op.drop_column("subscriptions", "pro_expires_at")
