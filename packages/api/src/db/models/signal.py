"""
Signal — persisted mirror of packages/enrichment/src/models/signal.py's
Signal dataclass. mantis:signals (Redis) is a capped, ephemeral display
log; this table is the durable record the track record / backtest
instrumentation and the analytics dashboard read from.

Field-for-field mirror of Signal.to_dict() so a row can round-trip through
the same canonical_json() used for the on-chain audit hash.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.models.base import Base


class SignalRow(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    chain:        Mapped[str] = mapped_column(String, nullable=False)
    protocol:     Mapped[str] = mapped_column(String, nullable=False)
    pool_address: Mapped[str] = mapped_column(String, nullable=False)
    wallets:      Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    signal_type: Mapped[str] = mapped_column(String, nullable=False)
    confidence:  Mapped[int] = mapped_column(Integer, nullable=False)

    summary:     Mapped[str] = mapped_column(String, nullable=False)
    key_factors: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deliver_at:  Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    z_score:          Mapped[float] = mapped_column(Float, nullable=False)
    total_volume_usd: Mapped[float] = mapped_column(Float, nullable=False)
    event_type:       Mapped[str] = mapped_column(String, nullable=False)

    audit_tx_hash: Mapped[str | None] = mapped_column(String, nullable=True)

    outcomes = relationship(
        "SignalOutcomeRow", back_populates="signal", cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict:
        return {
            "id":               self.id,
            "chain":            self.chain,
            "protocol":         self.protocol,
            "pool_address":     self.pool_address,
            "wallets":          self.wallets,
            "signal_type":      self.signal_type,
            "confidence":       self.confidence,
            "summary":          self.summary,
            "key_factors":      self.key_factors,
            "detected_at":      self.detected_at.isoformat(),
            "deliver_at":       self.deliver_at.isoformat(),
            "z_score":          self.z_score,
            "total_volume_usd": self.total_volume_usd,
            "event_type":       self.event_type,
            "audit_tx_hash":    self.audit_tx_hash,
        }
