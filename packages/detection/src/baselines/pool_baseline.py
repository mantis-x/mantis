"""
PoolBaseline — maintains a 14-day rolling baseline of inflow volume
per (pool_address, event_type) pair.

The baseline stores a sliding window of hourly volume buckets.
Mean and std are computed over the window for z-score calculation.

Storage: in-memory dict (sufficient for hackathon).
Production: replace with Postgres pool_baselines table.
"""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# 14 days × 24 hours = 336 hourly buckets
WINDOW_HOURS    = int(os.getenv("BASELINE_WINDOW_HOURS", "336"))
MIN_SAMPLES     = int(os.getenv("BASELINE_MIN_SAMPLES", "24"))   # need 1 day before scoring
BUCKET_SECONDS  = 3600  # 1-hour buckets


@dataclass
class BaselineStats:
    mean:   float
    std:    float
    count:  int   # number of non-zero buckets


class PoolBaseline:
    """
    Rolling baseline tracker for a single (pool, event_type) pair.
    Each bucket stores total USD volume in that hour.
    """
    def __init__(self, pool_address: str, event_type: str):
        self.pool_address = pool_address
        self.event_type   = event_type
        self._buckets: deque[float] = deque(maxlen=WINDOW_HOURS)
        self._current_bucket_ts: Optional[int] = None
        self._current_bucket_vol: float = 0.0

    def record(self, amount_usd: float, timestamp: float) -> None:
        """Add a volume observation. Buckets on 1-hour boundaries."""
        bucket_ts = int(timestamp // BUCKET_SECONDS) * BUCKET_SECONDS

        if self._current_bucket_ts is None:
            self._current_bucket_ts = bucket_ts

        if bucket_ts != self._current_bucket_ts:
            # Flush completed bucket
            self._buckets.append(self._current_bucket_vol)
            # Fill any missing hours with zeros
            missed = (bucket_ts - self._current_bucket_ts) // BUCKET_SECONDS - 1
            for _ in range(min(missed, WINDOW_HOURS)):
                self._buckets.append(0.0)
            self._current_bucket_ts = bucket_ts
            self._current_bucket_vol = 0.0

        self._current_bucket_vol += amount_usd

    def stats(self) -> Optional[BaselineStats]:
        """
        Return current baseline stats.
        Returns None if not enough data yet.
        """
        data = list(self._buckets)
        if len(data) < MIN_SAMPLES:
            return None

        arr  = np.array(data, dtype=float)
        mean = float(np.mean(arr))
        std  = float(np.std(arr))
        count = int(np.count_nonzero(arr))

        if std == 0:
            return None  # constant volume — can't z-score

        return BaselineStats(mean=mean, std=std, count=count)

    def z_score(self, observed_usd: float) -> Optional[float]:
        """Compute z-score for an observed volume against baseline."""
        s = self.stats()
        if s is None:
            return None
        return (observed_usd - s.mean) / s.std


class BaselineStore:
    """
    Registry of PoolBaseline instances, keyed by (chain, pool_address, event_type).
    One store per detection worker session.
    """

    def __init__(self):
        # (chain, pool_address, event_type) → PoolBaseline
        self._baselines: dict[tuple, PoolBaseline] = {}

    def record(
        self,
        pool_address: str,
        event_type:   str,
        amount_usd:   float,
        timestamp:    float,
        chain:        str = "mantle",
    ) -> None:
        key = (chain, pool_address.lower(), event_type)
        if key not in self._baselines:
            self._baselines[key] = PoolBaseline(pool_address, event_type)
        self._baselines[key].record(amount_usd, timestamp)

    def z_score(
        self,
        pool_address: str,
        event_type:   str,
        observed_usd: float,
        chain:        str = "mantle",
    ) -> Optional[float]:
        key = (chain, pool_address.lower(), event_type)
        bl  = self._baselines.get(key)
        if bl is None:
            return None
        return bl.z_score(observed_usd)

    def stats(
        self,
        pool_address: str,
        event_type:   str,
        chain:        str = "mantle",
    ) -> Optional[BaselineStats]:
        key = (chain, pool_address.lower(), event_type)
        bl  = self._baselines.get(key)
        return bl.stats() if bl else None

    def pool_count(self) -> int:
        return len(self._baselines)

    def seed_from_historical(self, historical_events: list) -> None:
        """
        Seed baselines from historical events so the detector
        can score immediately without waiting 14 days.
        Each event must have: pool_address, event_type, amount_usd, timestamp
        Optional: chain (default "mantle")
        """
        for e in historical_events:
            self.record(
                e["pool_address"],
                e["event_type"],
                e["amount_usd"],
                e["timestamp"],
                chain=e.get("chain", "mantle"),
            )
        log.info(
            "Baseline seeded from %d historical events across %d (chain, pool) pairs",
            len(historical_events), self.pool_count(),
        )
