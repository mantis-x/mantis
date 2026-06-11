"""
GuardRunner — runs all safety checks before any execution.
Returns (passed: bool, reason: str, guard_name: str).

Guards run in order:
  1. position_cap   — amount_usd <= max_position_pct of wallet
  2. slippage_check — slippage within tolerance
  3. blacklist      — target pool not flagged

First failure short-circuits. Abort reason is logged to ERC-8004.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

# Community blacklisted pools (rugpulls, exploited contracts)
BLACKLISTED_POOLS: set[str] = {
    # Add known bad actors here
    "0x0000000000000000000000000000000000000000",
}

DEFAULT_MAX_POSITION_PCT = float(os.getenv("DEFAULT_MAX_POSITION_PCT", "5"))
DEFAULT_MAX_SLIPPAGE_PCT = float(os.getenv("DEFAULT_MAX_SLIPPAGE_PCT", "2"))


class GuardResult:
    def __init__(self, passed: bool, reason: str = "", guard: str = ""):
        self.passed = passed
        self.reason = reason
        self.guard  = guard

    def __bool__(self):
        return self.passed


class GuardRunner:
    """
    Runs all safety guards in sequence.
    Instantiate once, call check() for each ExecutionRequest.
    """

    def check(self, request, wallet_balance_usd: float = 10_000.0) -> GuardResult:
        """
        Run all guards. Returns GuardResult — check .passed for outcome.

        wallet_balance_usd: current wallet balance for position cap calculation.
        In production this is fetched from the chain. Default 10K for testing.
        """
        # 1. Position cap
        result = self._position_cap(request, wallet_balance_usd)
        if not result:
            return result

        # 2. Slippage check
        result = self._slippage_check(request)
        if not result:
            return result

        # 3. Blacklist
        result = self._blacklist(request)
        if not result:
            return result

        return GuardResult(passed=True, reason="all guards passed")

    def _position_cap(
        self, request, wallet_balance_usd: float
    ) -> GuardResult:
        """Position size must not exceed max_position % of wallet."""
        max_pct    = request.max_position or DEFAULT_MAX_POSITION_PCT
        max_amount = wallet_balance_usd * (max_pct / 100)

        if request.amount_usd > max_amount:
            reason = (
                f"Position cap: ${request.amount_usd:.0f} exceeds "
                f"{max_pct:.0f}% of wallet (${max_amount:.0f})"
            )
            log.warning("Guard [position_cap]: %s", reason)
            return GuardResult(False, reason, "position_cap")

        return GuardResult(True)

    def _slippage_check(self, request) -> GuardResult:
        """
        Slippage guard. In production this fetches a live quote from
        Byreal and compares expected vs actual price.
        For hackathon: pass if max_slippage <= threshold.
        """
        max_slip = request.max_slippage or (DEFAULT_MAX_SLIPPAGE_PCT / 100)
        threshold = DEFAULT_MAX_SLIPPAGE_PCT / 100

        if max_slip > threshold:
            reason = (
                f"Slippage guard: {max_slip*100:.1f}% exceeds "
                f"threshold {threshold*100:.1f}%"
            )
            log.warning("Guard [slippage]: %s", reason)
            return GuardResult(False, reason, "slippage_check")

        return GuardResult(True)

    def _blacklist(self, request) -> GuardResult:
        """Pool must not be on the community blacklist."""
        pool = request.pool_address.lower()
        if pool in {p.lower() for p in BLACKLISTED_POOLS}:
            reason = f"Blacklist: pool {request.pool_address[:12]} is flagged"
            log.warning("Guard [blacklist]: %s", reason)
            return GuardResult(False, reason, "blacklist")

        return GuardResult(True)
