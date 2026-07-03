"""
Signal — the final enriched output of the Mantis Scout pipeline.
This is what gets delivered to Telegram users and logged on-chain.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional
import os


class SignalType(str, Enum):
    ACCUMULATION   = "accumulation"
    DISTRIBUTION   = "distribution"
    WHALE_ENTRY    = "whale_entry"
    WHALE_EXIT     = "whale_exit"
    UNUSUAL_VOLUME = "unusual_volume"


@dataclass
class Signal:
    """
    A fully enriched, delivery-ready signal.
    Created by the enrichment worker from an AnomalyCandidate.
    """
    # Identity
    id:           Optional[int]   # assigned when persisted to DB

    # Source
    chain:        str             # "mantle" | "arbitrum" | …
    protocol:     str             # "agni_finance" | "merchant_moe" | "fluxion"
    pool_address: str
    wallets:      list[str]       # wallet addresses in the cluster

    # Classification
    signal_type:  SignalType
    confidence:   int             # 0–100

    # Human-readable content
    summary:      str             # 2-sentence plain English
    key_factors:  list[str]       # 3 bullet points for Telegram card

    # Timing
    detected_at:  datetime
    deliver_at:   datetime        # detected_at + SIGNAL_HOLD_MINUTES

    # Detection metadata
    z_score:      float
    total_volume_usd: float
    event_type:   str

    # Audit (set after on-chain logging)
    audit_tx_hash: Optional[str] = None

    def __post_init__(self):
        if self.deliver_at is None:
            hold = int(os.getenv("SIGNAL_HOLD_MINUTES", "5"))
            self.deliver_at = self.detected_at + timedelta(minutes=hold)

    def canonical_json(self) -> str:
        """
        Stable JSON for keccak256 hashing — keys alphabetically ordered.
        Must match the Python delivery worker AND Solidity verify() function.
        "chain" is first alphabetically; adding it is backward-compatible with
        the contract because it hashes the full string opaquely.
        """
        import json
        return json.dumps({
            "chain":       self.chain,
            "confidence":  self.confidence,
            "deliver_at":  self.deliver_at.isoformat(),
            "id":          self.id or 0,
            "pool":        self.pool_address,
            "protocol":    self.protocol,
            "signal_type": self.signal_type.value,
            "summary":     self.summary,
        }, separators=(",", ":"))

    def to_dict(self) -> dict:
        return {
            "id":              self.id,
            "chain":           self.chain,
            "protocol":        self.protocol,
            "pool_address":    self.pool_address,
            "wallets":         self.wallets,
            "signal_type":     self.signal_type.value,
            "confidence":      self.confidence,
            "summary":         self.summary,
            "key_factors":     self.key_factors,
            "detected_at":     self.detected_at.isoformat(),
            "deliver_at":      self.deliver_at.isoformat(),
            "z_score":         self.z_score,
            "total_volume_usd": self.total_volume_usd,
            "event_type":      self.event_type,
            "audit_tx_hash":   self.audit_tx_hash,
        }

    @property
    def emoji(self) -> str:
        return {
            SignalType.ACCUMULATION:   "📈",
            SignalType.DISTRIBUTION:   "📉",
            SignalType.WHALE_ENTRY:    "🐋",
            SignalType.WHALE_EXIT:     "🚨",
            SignalType.UNUSUAL_VOLUME: "⚡",
        }.get(self.signal_type, "🔍")

    @property
    def protocol_display(self) -> str:
        return {
            "agni_finance":  "Agni Finance",
            "merchant_moe":  "Merchant Moe",
            "fluxion":       "Fluxion",
        }.get(self.protocol, self.protocol.title())
