"""
SignalOutcome — one row per (signal, horizon), tracking the price of the
signal's underlying asset from the moment the signal was detected out to a
fixed set of horizons (1h / 4h / 24h / 7d). This is what makes the track
record real: it's the only place in the whole pipeline a price is captured
and later re-checked, rather than thrown away after being used once to
compute a USD notional.

Signals themselves carry no per-token price (see Signal — only aggregate
total_volume_usd). The tracker resolves price_key from
ChainConfig.pool_registry[pool_address] (chain + pool_address are on every
Signal) and reads it via the same PriceOracle already used at ingestion —
no changes to the ingestion payload are required.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.models.base import Base

# Fixed horizons checked for every signal, in hours.
HORIZONS_HOURS = {
    "1h":  1,
    "4h":  4,
    "24h": 24,
    "7d":  24 * 7,
}


class OutcomeStatus:
    PENDING   = "pending"    # due_at not yet reached
    COMPLETED = "completed"  # price successfully captured at due_at
    FAILED    = "failed"     # due_at passed but price lookup failed (no feed / RPC error)


class SignalOutcomeRow(Base):
    __tablename__ = "signal_outcomes"

    id:        Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), nullable=False)

    horizon_label: Mapped[str] = mapped_column(String, nullable=False)  # "1h" | "4h" | "24h" | "7d"
    due_at:        Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    price_key:       Mapped[str] = mapped_column(String, nullable=False)   # e.g. "eth", "arb"
    entry_price_usd: Mapped[float] = mapped_column(Float, nullable=False)  # price at signal detection

    status:        Mapped[str] = mapped_column(String, nullable=False, default=OutcomeStatus.PENDING)
    price_usd:     Mapped[float | None] = mapped_column(Float, nullable=True)   # price at due_at, once checked
    pct_change:    Mapped[float | None] = mapped_column(Float, nullable=True)   # (price_usd - entry) / entry * 100
    checked_at:    Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    signal = relationship("SignalRow", back_populates="outcomes")
