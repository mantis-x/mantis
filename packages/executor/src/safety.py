"""
Execution safety primitives — kill switch, signal dedup, and daily spend
tracking. Split out from worker.py so the Redis-dependent logic here is
directly unit-testable (with fakeredis) rather than only reachable through
the full asyncio consume loop.

These are the operational safeguards from docs/execute_readiness.md's §3/§4
checklist that don't require touching live money to build — all three work
identically whether BYREAL_DRY_RUN is true or false.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

KILL_SWITCH_KEY     = "mantis:execution:kill_switch"
DEDUP_KEY_PREFIX     = "mantis:execution:seen:"
DAILY_SPENT_PREFIX   = "mantis:execution:daily_spent:"

# How long a signal's dedup lock lives — long enough to cover any plausible
# replay window (a stuck queue, a manual Redis restore), short enough not to
# accumulate keys forever.
SIGNAL_DEDUP_TTL_SECONDS = int(os.getenv("SIGNAL_DEDUP_TTL_SECONDS", str(24 * 3600)))

# Daily spend counters are read/written by UTC date; keep them around a
# little past 24h as a cheap safety net against clock skew, not as a feature.
DAILY_SPENT_TTL_SECONDS = 2 * 24 * 3600


def signal_fingerprint(signal: dict) -> str:
    """
    A stable identity for "this exact signal" — used to detect the same
    signal appearing twice on mantis:signals:exec (a replay, a manual Redis
    restore, a duplicate push) rather than trusting BLPOP's atomicity alone
    to guarantee exactly-once execution. Built from the same fields that
    make a signal what it is; deliberately excludes `id` (often None at this
    stage — assigned later by the tracking worker) and `audit_tx_hash`
    (not yet set when a signal reaches the exec queue).
    """
    key_fields = {
        "chain":            signal.get("chain"),
        "protocol":         signal.get("protocol"),
        "pool_address":     signal.get("pool_address"),
        "signal_type":      signal.get("signal_type"),
        "confidence":       signal.get("confidence"),
        "z_score":          signal.get("z_score"),
        "total_volume_usd": signal.get("total_volume_usd"),
        "detected_at":      signal.get("detected_at"),
    }
    canonical = json.dumps(key_fields, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def is_kill_switch_active(r) -> bool:
    """True if an operator has set the kill switch — halts execution with no redeploy."""
    return bool(await r.get(KILL_SWITCH_KEY))


async def is_duplicate_signal(r, signal: dict) -> bool:
    """
    True if this exact signal was already claimed for execution. Uses SETNX
    (via SET ... NX) so the check-and-claim is atomic — two workers (or two
    passes of the same worker) racing on the same signal can't both think
    they claimed it first.
    """
    key = DEDUP_KEY_PREFIX + signal_fingerprint(signal)
    claimed = await r.set(key, "1", nx=True, ex=SIGNAL_DEDUP_TTL_SECONDS)
    return not claimed  # claimed is None/False if the key already existed


def _daily_key(now: datetime | None = None) -> str:
    now = now or datetime.now(tz=timezone.utc)
    return DAILY_SPENT_PREFIX + now.strftime("%Y-%m-%d")


async def get_daily_spent_usd(r) -> float:
    """Cumulative USD executed today (UTC), for the absolute daily cap guard."""
    value = await r.get(_daily_key())
    return float(value) if value else 0.0


async def record_execution_spend(r, amount_usd: float) -> None:
    """Bump today's cumulative spend after a successful execution."""
    if not amount_usd:
        return
    key = _daily_key()
    await r.incrbyfloat(key, amount_usd)
    await r.expire(key, DAILY_SPENT_TTL_SECONDS)
