"""
NormalisedEvent — the canonical data model for every on-chain event
ingested from DeFi protocols across all supported chains.

This dataclass is the contract between ingestion and detection.
All protocol decoders must return NormalisedEvent instances.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class Protocol(str, Enum):
    # Mantle
    AGNI_FINANCE  = "agni_finance"
    MERCHANT_MOE  = "merchant_moe"
    FLUXION       = "fluxion"
    # Arbitrum (Phase 1)
    UNISWAP_V3    = "uniswap_v3"
    TRADER_JOE    = "trader_joe"


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

    # Chain
    chain:          str       # "mantle" | "arbitrum" | …

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
        """Dedup key — one event per (chain, tx, log_index)."""
        return f"{self.chain}:{self.tx_hash}:{self.log_index}"
