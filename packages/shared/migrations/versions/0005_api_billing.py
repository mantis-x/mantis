"""api tier phase C — api_customers.registered_wallet + api_payments

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-28

Self-serve billing for the API tier (docs/api_tier_build_plan.md Phase C).
Inert until API_TIER_PAYMENTS_ENABLED=true and API_TIER_RECEIVE_ADDRESS is set.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_customers", sa.Column("registered_wallet", sa.String, nullable=True))

    op.create_table(
        "api_payments",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("chain", sa.String, nullable=False),
        sa.Column("tx_hash", sa.String, nullable=False),
        sa.Column("log_index", sa.Integer, nullable=False),
        sa.Column("from_address", sa.String, nullable=False),
        sa.Column("amount_usdc", sa.Float, nullable=False),
        sa.Column("matched", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("credited_customer_id", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("chain", "tx_hash", "log_index", name="uq_api_payment_tx_log"),
    )


def downgrade() -> None:
    op.drop_table("api_payments")
    op.drop_column("api_customers", "registered_wallet")
