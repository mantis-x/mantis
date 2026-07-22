"""
GuardRunner — runs all safety checks before any execution.
Returns (passed: bool, reason: str, guard_name: str).

Guards run in order:
  1. position_cap        — amount_usd <= max_position_pct of wallet
  2. slippage_check       — requested slippage tolerance is within a sane bound
  3. blacklist            — target pool not flagged
  4. absolute_trade_cap   — amount_usd <= a hard per-trade USD ceiling
  5. absolute_daily_cap   — today's cumulative executed USD stays under a ceiling

First failure short-circuits. Abort reason is logged to ERC-8004.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

# Sentinel non-pool addresses that should never be a legitimate trade target.
_SENTINEL_BAD_ADDRESSES: set[str] = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",  # common burn address
}

# Real, maintained blacklist entries go in BLACKLIST_POOLS (comma-separated
# addresses) as an env var — e.g. from a security vendor feed or a manually
# curated list of confirmed rugpulls/exploited contracts. There is no
# hardcoded list of "known bad" addresses here: fabricating one from memory
# would be worse than an honest, empty-but-configurable list, since a wrong
# guess gives false confidence. This guard is a real no-op until an operator
# populates BLACKLIST_POOLS.
_env_blacklist = {
    addr.strip().lower()
    for addr in os.getenv("BLACKLIST_POOLS", "").split(",")
    if addr.strip()
}
BLACKLISTED_POOLS: set[str] = _SENTINEL_BAD_ADDRESSES | _env_blacklist

if not _env_blacklist:
    log.warning(
        "BLACKLIST_POOLS is empty — blacklist guard only blocks sentinel "
        "burn/zero addresses. Populate BLACKLIST_POOLS with real flagged "
        "pool addresses before relying on this guard in production."
    )

DEFAULT_MAX_POSITION_PCT = float(os.getenv("DEFAULT_MAX_POSITION_PCT", "5"))
DEFAULT_MAX_SLIPPAGE_PCT = float(os.getenv("DEFAULT_MAX_SLIPPAGE_PCT", "2"))

# Hard ceilings independent of the % guard above — a pricing bug or a
# wallet-balance lookup error can't blow past these regardless of how the
# percentage math works out. Defaults match docs/execute_readiness.md's own
# recommended starting range ("$10-50/trade") for the first live rollout;
# raise deliberately via env once execution has a track record.
MAX_TRADE_USD       = float(os.getenv("MAX_TRADE_USD", "50"))
MAX_DAILY_TRADE_USD = float(os.getenv("MAX_DAILY_TRADE_USD", "200"))


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

    def check(
        self, request, wallet_balance_usd: float, daily_spent_usd: float = 0.0,
    ) -> GuardResult:
        """
        Run all guards. Returns GuardResult — check .passed for outcome.

        wallet_balance_usd: the wallet's real, current USD balance for the
        request's chain. No default is provided deliberately — a silent
        fallback here previously let the position cap guard run against a
        fictitious $10,000 regardless of actual funds, which meant it was
        never really capping anything relative to the real wallet. Callers
        must fetch the real on-chain balance (see Executor._wallet_balance_usd)
        and pass it explicitly, or the guard fails closed at $0.

        daily_spent_usd: cumulative USD already executed today (UTC), for the
        absolute daily cap guard. Defaults to 0.0 so existing callers that
        don't track this yet see unchanged behavior on every other guard.
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

        # 4. Absolute per-trade cap
        result = self._absolute_trade_cap(request)
        if not result:
            return result

        # 5. Absolute daily cap
        result = self._absolute_daily_cap(request, daily_spent_usd)
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
        Pre-flight sanity bound on the requested slippage tolerance — this
        guard has no chain/RPC context, so it cannot itself fetch a live
        quote. That protection is real and already implemented where it
        belongs: arbitrum/swap_executor.py's _get_quote() calls Uniswap V3's
        QuoterV2 on-chain immediately before every swap and sets
        amountOutMinimum from that live quote, not from this guard. This
        check exists to reject an absurd/unset max_slippage before a
        request ever reaches the executor, not to duplicate that on-chain
        quote comparison.
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

    def _absolute_trade_cap(self, request) -> GuardResult:
        """
        Hard per-trade USD ceiling — independent of wallet size. Protects
        against a wallet-balance lookup bug or an unexpectedly large wallet
        making the percentage-based position_cap guard permissive in a way
        nobody intended for a first live rollout.
        """
        if request.amount_usd > MAX_TRADE_USD:
            reason = (
                f"Absolute trade cap: ${request.amount_usd:.0f} exceeds "
                f"hard ceiling ${MAX_TRADE_USD:.0f}"
            )
            log.warning("Guard [absolute_trade_cap]: %s", reason)
            return GuardResult(False, reason, "absolute_trade_cap")

        return GuardResult(True)

    def _absolute_daily_cap(self, request, daily_spent_usd: float) -> GuardResult:
        """Cumulative executed USD today (UTC) must stay under a hard ceiling."""
        if daily_spent_usd + request.amount_usd > MAX_DAILY_TRADE_USD:
            reason = (
                f"Absolute daily cap: ${daily_spent_usd:.0f} already spent today + "
                f"${request.amount_usd:.0f} would exceed ${MAX_DAILY_TRADE_USD:.0f}/day"
            )
            log.warning("Guard [absolute_daily_cap]: %s", reason)
            return GuardResult(False, reason, "absolute_daily_cap")

        return GuardResult(True)
