"""
NormalisedEvent — the canonical data model for every on-chain event
ingested from Mantle DeFi protocols.

This dataclass is the contract between ingestion and detection.
All protocol decoders must return NormalisedEvent instances.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class Protocol(str, Enum):
    AGNI_FINANCE  = "agni_finance"
    MERCHANT_MOE  = "merchant_moe"
    FLUXION       = "fluxion"


class EventType(str, Enum):
    SWAP  = "swap"
    MINT  = "mint"    # LP deposit
    BURN  = "burn"    # LP withdrawal


@dataclass
class NormalisedEvent:
    # Identity
    block_number:   int
    tx_hash:        str
    log_index:      int

    # Protocol
    protocol:       Protocol
    pool_address:   str       # checksummed

    # Actor
    wallet_address: str       # checksummed — sender/recipient

    # Event
    event_type:     EventType
    amount_usd:     float     # USD value at time of event

    # Token info (optional — populated when decodable)
    token_in:       Optional[str] = None
    token_out:      Optional[str] = None
    amount_in:      Optional[int] = None   # raw token units
    amount_out:     Optional[int] = None

    # Timestamps
    timestamp:      datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )

    def __post_init__(self):
        # Normalise addresses to lowercase for consistent comparison
        self.pool_address   = self.pool_address.lower()
        self.wallet_address = self.wallet_address.lower()
        self.tx_hash        = self.tx_hash.lower()

    @property
    def unique_id(self) -> str:
        """Dedup key — one event per (tx, log_index)."""
        return f"{self.tx_hash}:{self.log_index}"
