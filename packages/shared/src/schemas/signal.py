"""
Central Signal schema — the canonical data contract between all packages.
ingestion → detection → enrichment write to this.
delivery and executor read from this.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class SignalType(str, Enum):
    ACCUMULATION = "accumulation"
    DISTRIBUTION = "distribution"
    WHALE_ENTRY  = "whale_entry"
    WHALE_EXIT   = "whale_exit"
    UNUSUAL_VOLUME = "unusual_volume"


class Protocol(str, Enum):
    MERCHANT_MOE = "merchant_moe"
    AGNI_FINANCE = "agni_finance"
    FLUXION      = "fluxion"


@dataclass
class WalletCluster:
    wallets: list[str]          # list of wallet addresses
    pool_address: str
    protocol: Protocol
    total_volume_usd: float
    z_score: float
    event_type: str             # "swap" | "mint" | "burn"
    first_seen: datetime
    last_seen: datetime


@dataclass
class Signal:
    id: Optional[int]
    cluster: WalletCluster
    signal_type: SignalType
    confidence: int             # 0–100
    summary: str                # plain-English explanation
    key_factors: list[str]      # bullet points for Telegram card
    deliver_at: datetime        # now + SIGNAL_HOLD_MINUTES
    audit_tx_hash: Optional[str] = None   # set after on-chain log
    created_at: datetime = field(default_factory=datetime.utcnow)
