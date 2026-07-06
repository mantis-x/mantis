"""initial schema — agents, subscriptions, signals, signal_outcomes, executions

Revision ID: 0001
Revises:
Create Date: 2026-07-06

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("agent_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("owner_wallet", sa.String, nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("rules", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("erc8004_token_id", sa.BigInteger, nullable=True),
        sa.Column("total_decisions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_executions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_aborts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("channel", sa.String, nullable=False),
        sa.Column("recipient_id", sa.String, nullable=False),
        sa.Column("min_confidence", sa.Integer, nullable=False, server_default="60"),
        sa.Column("signal_types", postgresql.JSONB, nullable=True),
        sa.Column("protocols", postgresql.JSONB, nullable=True),
        sa.Column("chains", postgresql.JSONB, nullable=True),
        sa.Column("is_pro", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("alerts_today", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_alert_date", sa.Date, nullable=True),
        sa.UniqueConstraint("channel", "recipient_id", name="uq_subscription_channel_recipient"),
    )

    op.create_table(
        "signals",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("chain", sa.String, nullable=False),
        sa.Column("protocol", sa.String, nullable=False),
        sa.Column("pool_address", sa.String, nullable=False),
        sa.Column("wallets", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("signal_type", sa.String, nullable=False),
        sa.Column("confidence", sa.Integer, nullable=False),
        sa.Column("summary", sa.String, nullable=False),
        sa.Column("key_factors", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deliver_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("z_score", sa.Float, nullable=False),
        sa.Column("total_volume_usd", sa.Float, nullable=False),
        sa.Column("event_type", sa.String, nullable=False),
        sa.Column("audit_tx_hash", sa.String, nullable=True),
    )
    op.create_index("ix_signals_chain_protocol", "signals", ["chain", "protocol"])
    op.create_index("ix_signals_detected_at", "signals", ["detected_at"])

    op.create_table(
        "signal_outcomes",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("signal_id", sa.Integer, sa.ForeignKey("signals.id"), nullable=False),
        sa.Column("horizon_label", sa.String, nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price_key", sa.String, nullable=False),
        sa.Column("entry_price_usd", sa.Float, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="pending"),
        sa.Column("price_usd", sa.Float, nullable=True),
        sa.Column("pct_change", sa.Float, nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_signal_outcomes_signal_id", "signal_outcomes", ["signal_id"])
    op.create_index(
        "ix_signal_outcomes_due_status", "signal_outcomes", ["due_at", "status"]
    )

    op.create_table(
        "executions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("agent_id", sa.Integer, nullable=False),
        sa.Column("signal_id", sa.String, nullable=False),
        sa.Column("chain", sa.String, nullable=False, server_default="mantle"),
        sa.Column("action_type", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("tx_hash", sa.String, nullable=True),
        sa.Column("amount_usd", sa.Float, nullable=True),
        sa.Column("gas_used", sa.Integer, nullable=True),
        sa.Column("execution_price", sa.Float, nullable=True),
        sa.Column("abort_reason", sa.String, nullable=True),
        sa.Column("guard_failed", sa.String, nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_executions_agent_id", "executions", ["agent_id"])
    op.create_index("ix_executions_executed_at", "executions", ["executed_at"])


def downgrade() -> None:
    op.drop_table("executions")
    op.drop_table("signal_outcomes")
    op.drop_table("signals")
    op.drop_table("subscriptions")
    op.drop_table("agents")
