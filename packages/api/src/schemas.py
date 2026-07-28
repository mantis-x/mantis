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


# ── webhooks (Phase B) ──────────────────────────────────────────────────────
class EventFilters(BaseModel):
    chain: Optional[str] = None
    signal_type: Optional[str] = None
    min_confidence: Optional[int] = None


class WebhookCreateIn(BaseModel):
    url: str
    event_filters: Optional[EventFilters] = None


class WebhookOut(BaseModel):
    id: int
    url: str
    active: bool
    disabled: bool
    event_filters: Optional[dict] = None
    consecutive_failures: int
    created_at: str


class WebhookCreatedOut(WebhookOut):
    # The signing secret — shown ONCE at creation, never returned again.
    secret: str


class WebhookTestResultOut(BaseModel):
    delivered: bool
    status_code: Optional[int] = None


# ── billing (Phase C) ───────────────────────────────────────────────────────
class WalletRegisterIn(BaseModel):
    address: str


class PricingTierOut(BaseModel):
    days: int
    months: int
    price_usdc: float


class BillingInfoOut(BaseModel):
    payments_enabled: bool
    receive_address: Optional[str] = None        # None until configured
    currency: str = "USDC"
    chain: str = "arbitrum"
    tiers: list[PricingTierOut]
    registered_wallet: Optional[str] = None
    api_tier_expires_at: Optional[str] = None
    active: bool
