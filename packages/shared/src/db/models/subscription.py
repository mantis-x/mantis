"""
Subscription — persisted replacement for the three near-identical in-memory
SubscriptionManager stores (packages/delivery/src/common/subscription_manager.py,
packages/delivery/src/telegram/subscription_manager.py). One table serves all
three channels, discriminated by `channel`.

recipient_id is stored as text for all channels — Telegram's integer chat_id
is stringified on write and parsed back to int at the Telegram call site
(the only channel whose SDK requires an int chat_id to send a message).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models.base import Base

FREE_DAILY_LIMIT = 3


class SubscriptionRow(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("channel", "recipient_id", name="uq_subscription_channel_recipient"),
    )

    id:            Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel:       Mapped[str] = mapped_column(String, nullable=False)   # "telegram" | "discord" | "line"
    recipient_id:  Mapped[str] = mapped_column(String, nullable=False)

    min_confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    signal_types:   Mapped[list | None] = mapped_column(JSONB, nullable=True)  # None = all types
    protocols:      Mapped[list | None] = mapped_column(JSONB, nullable=True)  # None = all protocols
    chains:         Mapped[list | None] = mapped_column(JSONB, nullable=True)  # None = all chains
    is_pro:         Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # NULL = never expires (manual/grandfathered grant). Set on a credited
    # on-chain payment (see ProPaymentRow); the tracking worker's expiry
    # sweep flips is_pro back to False once this lapses — but only for rows
    # where this is NOT NULL, so manual grants are never touched.
    pro_expires_at:    Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Wallet address the subscriber registered via /register_wallet, used to
    # match an incoming USDC payment on Arbitrum to this row. Lowercased on write.
    registered_wallet: Mapped[str | None] = mapped_column(String, nullable=True)

    joined_at:       Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(tz=timezone.utc),
    )
    alerts_today:    Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_alert_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    def can_receive(self, signal: dict) -> bool:
        """Mirrors Subscription.can_receive from the in-memory implementation."""
        if signal.get("confidence", 0) < self.min_confidence:
            return False
        if self.signal_types and signal.get("signal_type") not in self.signal_types:
            return False
        if self.protocols and signal.get("protocol") not in self.protocols:
            return False
        if self.chains and signal.get("chain", "mantle") not in self.chains:
            return False
        if not self.is_pro:
            today = datetime.now(tz=timezone.utc).date()
            if self.last_alert_date == today and self.alerts_today >= FREE_DAILY_LIMIT:
                return False
        return True

    def record_alert(self) -> None:
        today = datetime.now(tz=timezone.utc).date()
        if self.last_alert_date != today:
            self.alerts_today = 0
            self.last_alert_date = today
        self.alerts_today += 1
