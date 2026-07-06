"""
Execution — persisted mirror of packages/executor/src/models/execution_result.py's
ExecutionResult. mantis:executions (Redis) is capped at 1000 and has no
durable consumer today — this table is the first one, and is what a
track record's realized-PnL side reads from (paired with SignalOutcome's
price-based, unrealized side).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models.base import Base


class ExecutionRow(Base):
    __tablename__ = "executions"

    id:         Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id:   Mapped[int] = mapped_column(Integer, nullable=False)
    signal_id:  Mapped[str] = mapped_column(String, nullable=False)
    chain:      Mapped[str] = mapped_column(String, nullable=False, default="mantle")
    action_type: Mapped[str] = mapped_column(String, nullable=False)
    status:     Mapped[str] = mapped_column(String, nullable=False)  # success | aborted | failed

    tx_hash:         Mapped[str | None] = mapped_column(String, nullable=True)
    amount_usd:      Mapped[float | None] = mapped_column(Float, nullable=True)
    gas_used:        Mapped[int | None] = mapped_column(Integer, nullable=True)
    execution_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    abort_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    guard_failed: Mapped[str | None] = mapped_column(String, nullable=True)

    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "agent_id":        self.agent_id,
            "signal_id":       self.signal_id,
            "chain":           self.chain,
            "action_type":     self.action_type,
            "status":          self.status,
            "tx_hash":         self.tx_hash,
            "amount_usd":      self.amount_usd,
            "gas_used":        self.gas_used,
            "execution_price": self.execution_price,
            "abort_reason":    self.abort_reason,
            "guard_failed":    self.guard_failed,
            "executed_at":     self.executed_at.isoformat(),
        }
