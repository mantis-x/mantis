"""
ProPaymentRow — one row per on-chain USDC transfer seen by the Pro-tier
payment watcher (packages/shared/src/billing/pro_payment_watcher.py).

Exists purely for idempotency + audit: the unique constraint on
(chain, tx_hash, log_index) means a re-poll or worker restart can never
credit the same transfer twice. `matched=False` rows are payments received
with no subscriber registered_wallet matching the sender — recorded so they
are not reprocessed, but requiring manual reconciliation to credit.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models.base import Base


class ProPaymentRow(Base):
    __tablename__ = "pro_payments"
    __table_args__ = (
        UniqueConstraint("chain", "tx_hash", "log_index", name="uq_pro_payment_tx_log"),
    )

    id:         Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chain:      Mapped[str] = mapped_column(String, nullable=False)
    tx_hash:    Mapped[str] = mapped_column(String, nullable=False)
    log_index:  Mapped[int] = mapped_column(Integer, nullable=False)

    from_address: Mapped[str] = mapped_column(String, nullable=False)
    amount_usdc:  Mapped[float] = mapped_column(Float, nullable=False)

    matched:               Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    credited_channel:      Mapped[str | None] = mapped_column(String, nullable=True)
    credited_recipient_id: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(tz=timezone.utc),
    )
