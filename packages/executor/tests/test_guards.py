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
    # Default amount_usd (25.0) clears the default $50 absolute trade cap —
    # see TestAbsoluteCaps below for tests that specifically exercise caps
    # at larger amounts via a raised MAX_TRADE_USD.
    request = make_request(amount_usd=25.0)
    result  = runner.check(request, wallet_balance_usd=10_000.0)
    assert result.passed, f"Expected pass: {result.reason}"


def test_position_cap_blocks_oversized_trade():
    runner  = GuardRunner()
    # 5% of 10K = $500 max. Request $600 — should fail at position_cap
    # (which runs before the absolute trade cap, so that's the guard that
    # actually fires here regardless of MAX_TRADE_USD's default).
    request = make_request(amount_usd=600.0, max_position=5.0)
    result  = runner.check(request, wallet_balance_usd=10_000.0)
    assert not result.passed
    assert result.guard == "position_cap"


def test_position_cap_allows_at_exact_limit(monkeypatch):
    """
    5% of 10K = $500 exactly — should clear position_cap. Raises
    MAX_TRADE_USD for this test specifically since $500 is deliberately
    larger than the (unrelated) default absolute trade cap and this test's
    only concern is position_cap's own boundary behavior.
    """
    monkeypatch.setenv("MAX_TRADE_USD", "100000")
    monkeypatch.setenv("MAX_DAILY_TRADE_USD", "100000")
    import importlib
    from src.guards import guard_runner as guard_runner_module
    importlib.reload(guard_runner_module)

    runner  = guard_runner_module.GuardRunner()
    request = make_request(amount_usd=500.0, max_position=5.0)
    result  = runner.check(request, wallet_balance_usd=10_000.0)
    assert result.passed, f"Expected pass: {result.reason}"

    monkeypatch.delenv("MAX_TRADE_USD", raising=False)
    monkeypatch.delenv("MAX_DAILY_TRADE_USD", raising=False)
    importlib.reload(guard_runner_module)


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


def test_burn_address_blocked():
    """The common 0x...dEaD burn address is a sentinel — never a real pool."""
    runner  = GuardRunner()
    request = make_request(
        pool_address="0x000000000000000000000000000000000000dEaD"
    )
    result  = runner.check(request, wallet_balance_usd=10_000.0)
    assert not result.passed
    assert result.guard == "blacklist"


def test_blacklist_pools_env_var_extends_blacklist(monkeypatch):
    """BLACKLIST_POOLS env var lets operators add real flagged addresses without a code change."""
    flagged = "0xBadBadBadBadBadBadBadBadBadBadBadBadBad0"
    monkeypatch.setenv("BLACKLIST_POOLS", flagged)

    import importlib
    from src.guards import guard_runner as guard_runner_module
    importlib.reload(guard_runner_module)

    runner  = guard_runner_module.GuardRunner()
    request = make_request(pool_address=flagged)
    result  = runner.check(request, wallet_balance_usd=10_000.0)
    assert not result.passed
    assert result.guard == "blacklist"

    # Reload again without the env var so later tests see the clean module state
    monkeypatch.delenv("BLACKLIST_POOLS", raising=False)
    importlib.reload(guard_runner_module)


def test_wallet_balance_usd_is_required_argument():
    """check() must not silently default to a fictitious balance."""
    import inspect
    sig = inspect.signature(GuardRunner.check)
    assert sig.parameters["wallet_balance_usd"].default is inspect.Parameter.empty


class TestAbsoluteCaps:
    """
    Hard USD ceilings independent of wallet size — protect against a
    wallet-balance bug or an unexpectedly large wallet making the
    percentage-based position_cap permissive in a way nobody intended.
    """

    def test_trade_cap_blocks_even_within_position_pct(self):
        # $600 is well within 5% of a $1M wallet ($50K) and clears every
        # other guard, but still exceeds the default $50 absolute cap.
        runner  = GuardRunner()
        request = make_request(amount_usd=600.0, max_position=5.0)
        result  = runner.check(request, wallet_balance_usd=1_000_000.0)
        assert not result.passed
        assert result.guard == "absolute_trade_cap"

    def test_trade_cap_allows_small_trade(self):
        runner  = GuardRunner()
        request = make_request(amount_usd=10.0, max_position=5.0)
        result  = runner.check(request, wallet_balance_usd=1_000_000.0)
        assert result.passed, f"Expected pass: {result.reason}"

    def test_trade_cap_allows_at_exact_limit(self):
        from src.guards.guard_runner import MAX_TRADE_USD
        runner  = GuardRunner()
        request = make_request(amount_usd=MAX_TRADE_USD, max_position=5.0)
        result  = runner.check(request, wallet_balance_usd=1_000_000.0)
        assert result.passed, f"Expected pass: {result.reason}"

    def test_daily_cap_blocks_when_cumulative_would_exceed(self):
        # $190 already spent today + a $20 trade would hit $210 > $200 default.
        runner  = GuardRunner()
        request = make_request(amount_usd=20.0, max_position=5.0)
        result  = runner.check(request, wallet_balance_usd=1_000_000.0, daily_spent_usd=190.0)
        assert not result.passed
        assert result.guard == "absolute_daily_cap"

    def test_daily_cap_allows_when_within_budget(self):
        runner  = GuardRunner()
        request = make_request(amount_usd=20.0, max_position=5.0)
        result  = runner.check(request, wallet_balance_usd=1_000_000.0, daily_spent_usd=100.0)
        assert result.passed, f"Expected pass: {result.reason}"

    def test_daily_cap_defaults_to_zero_spent(self):
        """Callers that don't pass daily_spent_usd get the pre-existing behavior."""
        runner  = GuardRunner()
        request = make_request(amount_usd=20.0, max_position=5.0)
        result  = runner.check(request, wallet_balance_usd=1_000_000.0)
        assert result.passed, f"Expected pass: {result.reason}"


if __name__ == "__main__":
    test_normal_request_passes_all_guards()
    test_position_cap_blocks_oversized_trade()
    test_position_cap_allows_at_exact_limit()
    test_slippage_guard_blocks_high_slippage()
    test_blacklisted_pool_blocked()
    test_guard_result_bool()
    print("✓ All guard tests passed")
