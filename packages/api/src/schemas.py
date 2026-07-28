"""Pydantic response models — the public shape of the API (also what FastAPI
renders into the OpenAPI/Swagger docs a customer reads)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class OutcomeOut(BaseModel):
    horizon: str            # "1h" | "4h" | "24h" | "7d"
    status: str             # pending | completed | failed
    pct_change: Optional[float] = None    # signed % move of the underlying at the horizon
    entry_price_usd: float
    price_usd: Optional[float] = None


class SignalOut(BaseModel):
    id: int
    chain: str
    protocol: str
    pool_address: str
    wallets: list[str]
    signal_type: str
    confidence: int
    summary: str
    key_factors: list[str]
    detected_at: str
    z_score: float
    total_volume_usd: float
    event_type: str
    audit_tx_hash: Optional[str] = None
    outcomes: list[OutcomeOut] = []


class SignalListOut(BaseModel):
    data: list[SignalOut]
    # Opaque cursor for the next page (pass back as ?cursor=). None = no more.
    next_cursor: Optional[str] = None


class ChainStatOut(BaseModel):
    chain: str
    signals: int
    resolved: int           # outcomes with a computed 24h pct_change
    hit_rate: Optional[float] = None   # fraction of resolved directional calls that moved the "right" way


class StatsOut(BaseModel):
    total_signals: int
    by_chain: list[ChainStatOut]


class HealthOut(BaseModel):
    status: str
    api_tier_enabled: bool
