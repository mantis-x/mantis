"""api tier — api_customers + api_keys

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-28

Standalone API-tier customer entity + keys (docs/api_tier_readiness.md §3;
docs/api_tier_build_plan.md task A1). Independent of subscriptions — an API
customer is headless. Nothing here is reachable until API_TIER_ENABLED=true
and the api worker is serving; the tables just exist so keys can be minted.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_customers",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("label", sa.String, nullable=False),
        sa.Column("contact", sa.String, nullable=True),
        sa.Column("api_tier_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("customer_id", sa.Integer, sa.ForeignKey("api_customers.id"), nullable=False),
        sa.Column("key_hash", sa.String, nullable=False),
        sa.Column("key_prefix", sa.String, nullable=False),
        sa.Column("label", sa.String, nullable=True),
        sa.Column("rate_limit_per_min", sa.Integer, nullable=False, server_default="60"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("key_hash", name="uq_api_key_hash"),
    )
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"])


def downgrade() -> None:
    op.drop_index("ix_api_keys_key_hash", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_table("api_customers")
