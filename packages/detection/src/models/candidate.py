"""
Detection data models.

ScoredEvent   — a single event flagged by the z-score detector
WalletCluster — a group of wallets that co-moved suspiciously
AnomalyCandidate — a cluster ready for LLM enrichment
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class SignalType(str, Enum):
    ACCUMULATION      = "accumulation"
    DISTRIBUTION      = "distribution"
    WHALE_ENTRY       = "whale_entry"
    WHALE_EXIT        = "whale_exit"
    UNUSUAL_VOLUME    = "unusual_volume"
    # mint/burn are liquidity add/remove, not directional buys/sells — kept
    # distinct from the swap-only directional types above.
    LIQUIDITY_ADDED   = "liquidity_added"
    LIQUIDITY_REMOVED = "liquidity_removed"


@dataclass
class ScoredEvent:
    """One normalised event that exceeded the z-score threshold."""
    # From ingestion
    block_number:   int
    tx_hash:        str
    chain:          str
    protocol:       str
    pool_address:   str
    wallet_address: str
    event_type:     str
    amount_usd:     float
    timestamp:      datetime

    # Added by detector
    z_score:        float
    baseline_mean:  float
    baseline_std:   float

    # True if z_score cleared the (per-chain) anomaly threshold. Sub-threshold
    # events are still emitted by ZScoreDetector.evaluate() so the multi-wallet
    # path can aggregate them (individually-modest but collectively-coordinated
    # activity) — only anomalies drive the solo-candidate path.
    is_anomaly:     bool = False

    @property
    def is_buy(self) -> bool:
        return self.event_type in ("swap", "mint")

    @property
    def is_sell(self) -> bool:
        return self.event_type == "burn"


@dataclass
class WalletCluster:
    """
    A group of wallets that independently performed similar actions
    on related pools within a short time window.
    Clusters are the unit passed to the LLM enricher.
    """
    wallets:          list[str]       # wallet addresses
    chain:            str
    pool_address:     str
    protocol:         str
    event_type:       str
    total_volume_usd: float
    z_score:          float           # highest z-score in the cluster
    event_count:      int
    first_seen:       datetime
    last_seen:        datetime

    # Derived signal type — set after clustering
    signal_type: SignalType = SignalType.UNUSUAL_VOLUME

    def __post_init__(self):
        # Classify signal type based on event type and volume. mint/burn are
        # liquidity add/remove, not directional buys/sells, so they get their
        # own non-directional types rather than being folded into
        # accumulation/whale_entry/distribution/whale_exit.
        if self.event_type == "swap":
            if self.total_volume_usd > 500_000:
                self.signal_type = SignalType.WHALE_ENTRY
            else:
                self.signal_type = SignalType.ACCUMULATION
        elif self.event_type == "mint":
            self.signal_type = SignalType.LIQUIDITY_ADDED
        elif self.event_type == "burn":
            self.signal_type = SignalType.LIQUIDITY_REMOVED

    @property
    def wallet_count(self) -> int:
        return len(self.wallets)

    @property
    def duration_seconds(self) -> float:
        return (self.last_seen - self.first_seen).total_seconds()


@dataclass
class AnomalyCandidate:
    """
    A wallet cluster ready to be sent to the enrichment worker.
    Includes the raw events for context building.
    """
    cluster:       WalletCluster
    raw_events:    list[ScoredEvent]
    detected_at:   datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
    # Optional: wallet metadata fetched from Nansen
    wallet_labels: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialise for Redis queue."""
        return {
            "chain":            self.cluster.chain,
            "wallets":          self.cluster.wallets,
            "pool_address":     self.cluster.pool_address,
            "protocol":         self.cluster.protocol,
            "event_type":       self.cluster.event_type,
            "total_volume_usd": self.cluster.total_volume_usd,
            "z_score":          self.cluster.z_score,
            "event_count":      self.cluster.event_count,
            "signal_type":      self.cluster.signal_type.value,
            "first_seen":       self.cluster.first_seen.isoformat(),
            "last_seen":        self.cluster.last_seen.isoformat(),
            "detected_at":      self.detected_at.isoformat(),
            "wallet_labels":    self.wallet_labels,
        }
