"""
SubscriptionRow — mirrors packages/shared/src/db/models/subscription.py
exactly (same table, same columns). This package doesn't run Alembic
itself; the table is created by packages/shared's migrations. Keep the
two files in sync.
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
    channel:       Mapped[str] = mapped_column(String, nullable=False)
    recipient_id:  Mapped[str] = mapped_column(String, nullable=False)

    min_confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    signal_types:   Mapped[list | None] = mapped_column(JSONB, nullable=True)
    protocols:      Mapped[list | None] = mapped_column(JSONB, nullable=True)
    chains:         Mapped[list | None] = mapped_column(JSONB, nullable=True)
    is_pro:         Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # NULL = never expires (manual/grandfathered grant); see shared model for full note.
    pro_expires_at:    Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    registered_wallet: Mapped[str | None] = mapped_column(String, nullable=True)

    joined_at:       Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(tz=timezone.utc),
    )
    alerts_today:    Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_alert_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    def can_receive(self, signal: dict) -> bool:
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
