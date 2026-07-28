"""
WebhookDeliveryRow — one row per (webhook, signal), tracking delivery of that
signal to that endpoint. The UniqueConstraint(webhook_id, signal_id) is the
idempotency key: a signal is never delivered to the same webhook twice, even
across dispatcher restarts / queue re-reads. This is why the fan-out payload
must carry the durable DB signal id (fanned out from the tracking worker after
persist, not from enrichment where the id is still None).

status: pending → delivered | failed (retriable) → exhausted (gave up).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.models.base import Base


class DeliveryStatus:
    PENDING   = "pending"
    DELIVERED = "delivered"
    FAILED    = "failed"      # attempt failed but retries remain
    EXHAUSTED = "exhausted"   # all attempts used, given up


class WebhookDeliveryRow(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        UniqueConstraint("webhook_id", "signal_id", name="uq_webhook_delivery_signal"),
    )

    id:         Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    webhook_id: Mapped[int] = mapped_column(ForeignKey("webhooks.id"), nullable=False, index=True)
    signal_id:  Mapped[int] = mapped_column(Integer, nullable=False)

    status:   Mapped[str] = mapped_column(String, nullable=False, default=DeliveryStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at:   Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    response_code:   Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(tz=timezone.utc),
    )

    webhook = relationship("WebhookRow", back_populates="deliveries")
