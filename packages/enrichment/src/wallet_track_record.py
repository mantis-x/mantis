"""
Wallet track-record labeling — a free, self-referential alternative to
Nansen's paid smart_money labels. A wallet counts as "smart money" here if
it has shown up in enough of Mantis's own past signals whose directional
prediction later resolved correctly.

The counters this reads are written by
packages/shared/src/tracking/signal_outcome_tracker.py's
_record_wallet_outcomes(), at the WALLET_TRACK_RECORD_HORIZON (24h) —
every wallet in a directional signal's cluster gets a hit/total bump once
that horizon's price check completes.

Needs no API key: it reads REDIS_URL, which is already required
infrastructure for every worker in this system. Optional in the same sense
Nansen is — if Redis is unreachable or a wallet has too few samples, this
returns None ("no opinion yet"), never a false negative.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import redis

log = logging.getLogger(__name__)

WALLET_HITS_KEY  = "mantis:wallet_track_record:hits"
WALLET_TOTAL_KEY = "mantis:wallet_track_record:total"

# A wallet needs at least this many resolved directional calls before its
# hit rate is trusted enough to label it smart money — otherwise one lucky
# trade would flip the label.
MIN_SAMPLES        = int(os.getenv("WALLET_TRACK_RECORD_MIN_SAMPLES", "3"))
HIT_RATE_THRESHOLD = float(os.getenv("WALLET_TRACK_RECORD_HIT_RATE", "0.65"))


class WalletTrackRecordLabeler:
    """Labels smart-money wallets from Mantis's own signal-outcome history."""

    def __init__(self, redis_url: str = ""):
        redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._redis = redis.from_url(redis_url, decode_responses=True)

    def is_smart_money(self, address: str) -> Optional[bool]:
        """
        True/False once the wallet has at least MIN_SAMPLES resolved
        directional calls; None if there aren't enough samples yet or the
        Redis lookup fails — callers must treat None as "unknown", never
        coerce it to "not smart money".
        """
        key = address.lower()
        try:
            pipe = self._redis.pipeline()
            pipe.hget(WALLET_HITS_KEY, key)
            pipe.hget(WALLET_TOTAL_KEY, key)
            hits_raw, total_raw = pipe.execute()
        except Exception as exc:
            log.warning("Wallet track-record lookup failed for %s: %s", address, exc)
            return None

        total = int(total_raw or 0)
        if total < MIN_SAMPLES:
            return None
        hits = int(hits_raw or 0)
        return (hits / total) >= HIT_RATE_THRESHOLD
