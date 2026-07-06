"""
AgentRow — mirrors packages/shared/src/db/models/agent.py exactly (same
table, same columns). This package doesn't run Alembic itself; the table
is created by packages/shared's migrations. Keep the two files in sync.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models.base import Base


class AgentRow(Base):
    __tablename__ = "agents"

    agent_id:     Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_wallet: Mapped[str] = mapped_column(String, nullable=False)
    name:         Mapped[str] = mapped_column(String, nullable=False)
    rules:        Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    active:       Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    erc8004_token_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    total_decisions:  Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_executions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_aborts:     Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(tz=timezone.utc),
    )
