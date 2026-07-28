"""
ApiCustomerRow — a standalone API-tier customer (the $299/mo institutional
feed + webhooks tier). Deliberately NOT a subscriptions row: an API customer
is headless (no Telegram/Discord/LINE channel), so it gets its own entity
with its own lifecycle. Decided 2026-07-24 — see docs/api_tier_readiness.md §3.

Billing (Phase C) sets api_tier_expires_at; a NULL expiry means "no expiry"
(a comped pilot / design-partner key, mirroring how subscriptions.pro_expires_at
NULL grandfathers a manually-flagged row).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.models.base import Base


class ApiCustomerRow(Base):
    __tablename__ = "api_customers"

    id:      Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label:   Mapped[str] = mapped_column(String, nullable=False)             # human-readable name
    contact: Mapped[str | None] = mapped_column(String, nullable=True)       # email / handle, optional

    # NULL = never expires (comped pilot). Phase C billing sets this from payments.
    api_tier_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # The Arbitrum wallet this customer pays from — the ApiPaymentWatcher matches
    # an incoming USDC transfer's sender to this to credit the right customer.
    # Set via POST /v1/billing/wallet. Stored lowercased.
    registered_wallet: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(tz=timezone.utc),
    )

    keys = relationship(
        "ApiKeyRow", back_populates="customer", cascade="all, delete-orphan"
    )

    def is_active(self, now: datetime | None = None) -> bool:
        """A customer is active if their tier hasn't lapsed (NULL = never lapses)."""
        if self.api_tier_expires_at is None:
            return True
        now = now or datetime.now(tz=timezone.utc)
        return self.api_tier_expires_at > now
