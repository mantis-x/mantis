"""
ExecutionResult — the outcome of an execution attempt.
Both successes and aborts produce a result — aborts are not silent.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class ResultStatus(str, Enum):
    SUCCESS = "success"
    ABORTED = "aborted"
    FAILED  = "failed"


@dataclass
class ExecutionResult:
    agent_id:       int
    signal_id:      str
    action_type:    str
    status:         ResultStatus

    # On success
    tx_hash:        Optional[str]   = None
    amount_usd:     Optional[float] = None
    gas_used:       Optional[int]   = None
    execution_price:Optional[float] = None

    # On abort/failure
    abort_reason:   Optional[str]   = None
    guard_failed:   Optional[str]   = None  # which guard triggered

    # Timing
    executed_at: datetime = None

    def __post_init__(self):
        if self.executed_at is None:
            self.executed_at = datetime.now(tz=timezone.utc)

    @property
    def success(self) -> bool:
        return self.status == ResultStatus.SUCCESS

    def to_dict(self) -> dict:
        return {
            "agent_id":        self.agent_id,
            "signal_id":       self.signal_id,
            "action_type":     self.action_type,
            "status":          self.status.value,
            "tx_hash":         self.tx_hash,
            "amount_usd":      self.amount_usd,
            "gas_used":        self.gas_used,
            "execution_price": self.execution_price,
            "abort_reason":    self.abort_reason,
            "guard_failed":    self.guard_failed,
            "executed_at":     self.executed_at.isoformat(),
        }

    @classmethod
    def aborted(cls, request, reason: str, guard: str = "") -> "ExecutionResult":
        return cls(
            agent_id     = request.agent_id,
            signal_id    = request.signal_id,
            action_type  = request.action_type.value,
            status       = ResultStatus.ABORTED,
            abort_reason = reason,
            guard_failed = guard,
        )

    @classmethod
    def success_from(
        cls, request, tx_hash: str, amount_usd: float,
        execution_price: Optional[float] = None,
    ) -> "ExecutionResult":
        return cls(
            agent_id        = request.agent_id,
            signal_id       = request.signal_id,
            action_type     = request.action_type.value,
            status          = ResultStatus.SUCCESS,
            tx_hash         = tx_hash,
            amount_usd      = amount_usd,
            execution_price = execution_price,
        )
