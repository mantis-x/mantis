"""
WebhookRow — a customer-registered push endpoint for the API tier (Phase B).
Belongs to an ApiCustomerRow. Each new signal is POSTed to every active
webhook whose event_filters match, HMAC-signed with `secret`.

Unlike api_keys, `secret` is stored in usable form (not hashed): it's a shared
signing secret — we need it to compute the HMAC on every delivery, and the
customer needs the same value to verify. Same model as Stripe/GitHub webhook
signing secrets. Shown once at registration.

Auto-disable: consecutive_failures counts delivery attempts that exhausted all
retries; it resets to 0 on any successful delivery. Crossing the disable
threshold flips active=False + stamps disabled_at (see webhook_dispatcher.py).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.models.base import Base


class WebhookRow(Base):
    __tablename__ = "webhooks"

    id:          Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("api_customers.id"), nullable=False, index=True)

    url:    Mapped[str] = mapped_column(String, nullable=False)
    secret: Mapped[str] = mapped_column(String, nullable=False)   # signing secret (stored usable)

    active:      Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Optional delivery filters: {"chain": "...", "signal_type": "...", "min_confidence": N}.
    # NULL/empty = receive everything.
    event_filters: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(tz=timezone.utc),
    )

    customer   = relationship("ApiCustomerRow")
    deliveries = relationship(
        "WebhookDeliveryRow", back_populates="webhook", cascade="all, delete-orphan"
    )
