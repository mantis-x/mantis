import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.executor import Executor
from src.models.execution_request import ActionType, ExecutionRequest
from src.approval.world_id import ApprovalDecision, ApprovalStatus


def _request(**overrides):
    values = dict(
        agent_id=1, owner_wallet="0xabc", signal_id="42", signal_type="whale_entry",
        protocol="uniswap_v3", pool_address="0xpool", confidence=90, z_score=4.0,
        action_type=ActionType.SWAP, amount_usd=5000, max_slippage=.02, max_position=5,
    )
    values.update(overrides)
    return ExecutionRequest(**values)


def _executor():
    executor = Executor.__new__(Executor)
    executor.guards = MagicMock()
    executor.guards.check.return_value = MagicMock(__bool__=lambda self: True)
    executor.arb_executor = MagicMock()
    executor.arb_executor.get_wallet_balance_usd.return_value = 100_000
    executor.byreal = MagicMock()
    executor.approval_gate = MagicMock()
    return executor


def test_unapproved_trade_never_reaches_uniswap():
    executor = _executor()
    executor.approval_gate.check.return_value = ApprovalDecision(
        ApprovalStatus.DENIED, "user denied"
    )
    result = executor._execute(_request(chain="arbitrum"))
    assert not result.success
    assert result.approval_status == "denied"
    assert result.guard_failed == "world_id_approval"
    executor.arb_executor.swap.assert_not_called()
    executor.byreal.swap_execute.assert_not_called()


def test_approved_trade_reaches_uniswap():
    executor = _executor()
    executor.approval_gate.check.return_value = ApprovalDecision(ApprovalStatus.APPROVED)
    executor.arb_executor.swap.return_value = {"tx_hash": "0xswap"}
    result = executor._execute(_request(chain="arbitrum", amount_usd=10))
    assert result.success
    executor.arb_executor.swap.assert_called_once()
