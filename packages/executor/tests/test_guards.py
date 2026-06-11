"""Tests for the safety guard system."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.guards.guard_runner import GuardRunner
from src.models.execution_request import ExecutionRequest, ActionType
from datetime import datetime, timezone


def make_request(**kwargs):
    defaults = dict(
        agent_id     = 0,
        owner_wallet = "0xabc",
        signal_id    = "1",
        signal_type  = "accumulation",
        protocol     = "agni_finance",
        pool_address = "0xcda86a272531e8640cd7f1a92c01839911b90bb0",
        confidence   = 82,
        z_score      = 3.8,
        action_type  = ActionType.SWAP,
        amount_usd   = 500.0,
        max_slippage = 0.02,
        max_position = 5.0,
    )
    defaults.update(kwargs)
    return ExecutionRequest(**defaults)


def test_normal_request_passes_all_guards():
    runner  = GuardRunner()
    request = make_request(amount_usd=500.0)
    result  = runner.check(request, wallet_balance_usd=10_000.0)
    assert result.passed, f"Expected pass: {result.reason}"


def test_position_cap_blocks_oversized_trade():
    runner  = GuardRunner()
    # 5% of 10K = $500 max. Request $600 — should fail.
    request = make_request(amount_usd=600.0, max_position=5.0)
    result  = runner.check(request, wallet_balance_usd=10_000.0)
    assert not result.passed
    assert result.guard == "position_cap"


def test_position_cap_allows_at_exact_limit():
    runner  = GuardRunner()
    # 5% of 10K = $500 exactly — should pass.
    request = make_request(amount_usd=500.0, max_position=5.0)
    result  = runner.check(request, wallet_balance_usd=10_000.0)
    assert result.passed


def test_slippage_guard_blocks_high_slippage():
    runner  = GuardRunner()
    # max_slippage 5% > threshold 2% — should fail
    request = make_request(max_slippage=0.05)
    result  = runner.check(request, wallet_balance_usd=10_000.0)
    assert not result.passed
    assert result.guard == "slippage_check"


def test_blacklisted_pool_blocked():
    runner  = GuardRunner()
    request = make_request(
        pool_address="0x0000000000000000000000000000000000000000"
    )
    result  = runner.check(request, wallet_balance_usd=10_000.0)
    assert not result.passed
    assert result.guard == "blacklist"


def test_guard_result_bool():
    from src.guards.guard_runner import GuardResult
    assert bool(GuardResult(True))  is True
    assert bool(GuardResult(False)) is False


if __name__ == "__main__":
    test_normal_request_passes_all_guards()
    test_position_cap_blocks_oversized_trade()
    test_position_cap_allows_at_exact_limit()
    test_slippage_guard_blocks_high_slippage()
    test_blacklisted_pool_blocked()
    test_guard_result_bool()
    print("✓ All guard tests passed")
