"""
ZScoreDetector — scores incoming NormalisedEvents against the
14-day rolling baseline and flags anomalies above the threshold.

Z-score = (observed_volume - baseline_mean) / baseline_std

Events above ZSCORE_THRESHOLD are returned as ScoredEvent instances.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from src.baselines.pool_baseline import BaselineStore
from src.models.candidate import ScoredEvent

log = logging.getLogger(__name__)

ZSCORE_THRESHOLD = float(os.getenv("ZSCORE_THRESHOLD", "2.5"))


class ZScoreDetector:
    """
    Stateless scorer — all state lives in BaselineStore.
    Call score_event() for each incoming event.
    """

    def __init__(self, baseline_store: BaselineStore):
        self._store     = baseline_store
        self._threshold = ZSCORE_THRESHOLD
        self._scored    = 0
        self._flagged   = 0

    def score_event(self, event) -> Optional[ScoredEvent]:
        """
        Score one event. Returns ScoredEvent if anomalous, else None.
        Also records the event into the baseline regardless of score.
        """
        ts = event.timestamp.timestamp()

        # Always update baseline
        self._store.record(
            event.pool_address,
            event.event_type.value if hasattr(event.event_type, 'value') else event.event_type,
            event.amount_usd,
            ts,
        )
        self._scored += 1

        # Score against baseline
        z = self._store.z_score(
            event.pool_address,
            event.event_type.value if hasattr(event.event_type, 'value') else event.event_type,
            event.amount_usd,
        )

        if z is None:
            return None   # not enough baseline data yet

        if z < self._threshold:
            return None   # normal activity

        stats = self._store.stats(
            event.pool_address,
            event.event_type.value if hasattr(event.event_type, 'value') else event.event_type,
        )

        self._flagged += 1
        log.info(
            "⚡ Anomaly z=%.2f pool=%s type=%s usd=%.0f",
            z, event.pool_address[:12], event.event_type, event.amount_usd,
        )

        return ScoredEvent(
            block_number   = event.block_number,
            tx_hash        = event.tx_hash,
            protocol       = event.protocol.value if hasattr(event.protocol, 'value') else event.protocol,
            pool_address   = event.pool_address,
            wallet_address = event.wallet_address,
            event_type     = event.event_type.value if hasattr(event.event_type, 'value') else event.event_type,
            amount_usd     = event.amount_usd,
            timestamp      = event.timestamp,
            z_score        = round(z, 3),
            baseline_mean  = round(stats.mean, 2) if stats else 0.0,
            baseline_std   = round(stats.std, 2)  if stats else 0.0,
        )

    def score_batch(self, events: list) -> list[ScoredEvent]:
        """Score a batch of events. Returns only the flagged ones."""
        results = []
        for e in events:
            scored = self.score_event(e)
            if scored:
                results.append(scored)
        return results

    @property
    def stats(self) -> dict:
        return {
            "scored":   self._scored,
            "flagged":  self._flagged,
            "flag_rate": round(self._flagged / max(self._scored, 1) * 100, 2),
            "threshold": self._threshold,
        }
