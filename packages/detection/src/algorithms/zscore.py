"""
ZScoreDetector: computes z-scores for inflow volume against a 14-day rolling
baseline per (pool_address, event_type) and flags anomalous events.

Z-score = (observed_volume - baseline_mean) / baseline_std

Events above ZSCORE_THRESHOLD (default 2.5) are flagged as candidates.
If std == 0 (constant pool activity), the event is not flagged.
"""
from __future__ import annotations
import logging
import os
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

ZSCORE_THRESHOLD = float(os.getenv("ZSCORE_THRESHOLD", "2.5"))


@dataclass
class ScoredEvent:
    event_id:     int
    pool_address: str
    event_type:   str
    wallet:       str
    amount_usd:   float
    z_score:      float
    baseline_mean: float
    baseline_std:  float


class ZScoreDetector:
    def __init__(self, baseline_store):
        """
        baseline_store: object with method
          get(pool_address, event_type) -> (mean, std) or None
        """
        self._store = baseline_store

    def score(self, event) -> Optional[ScoredEvent]:
        """Score one event. Returns ScoredEvent if above threshold, else None."""
        baseline = self._store.get(event.pool_address, event.event_type)
        if baseline is None:
            return None  # no baseline yet for this pool

        mean, std = baseline
        if std == 0:
            return None  # no variance — skip

        z = (event.amount_usd - mean) / std
        if z < ZSCORE_THRESHOLD:
            return None

        log.info(
            "Anomaly: pool=%s type=%s z=%.2f amount=$%.0f",
            event.pool_address[:10], event.event_type, z, event.amount_usd,
        )
        return ScoredEvent(
            event_id=event.id,
            pool_address=event.pool_address,
            event_type=event.event_type,
            wallet=event.wallet_address,
            amount_usd=event.amount_usd,
            z_score=round(z, 3),
            baseline_mean=round(mean, 2),
            baseline_std=round(std, 2),
        )

    def score_batch(self, events: list) -> list[ScoredEvent]:
        results = []
        for e in events:
            scored = self.score(e)
            if scored:
                results.append(scored)
        return results
