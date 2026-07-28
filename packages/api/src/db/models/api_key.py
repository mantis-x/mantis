"""
ApiKeyRow — one API key belonging to an ApiCustomerRow.

Security: the plaintext key is shown exactly once (at mint time) and NEVER
stored. We store `key_hash` (sha256 hex of the plaintext) and `key_prefix`
(the first chars, for display/debugging only — not a secret). Auth looks a
key up by hashing the presented value and matching `key_hash` directly, so
a database compromise never yields usable keys. See
packages/shared/src/security/api_keys.py for generation/hashing.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.models.base import Base

# Default per-key request budget; overridable per row (higher for bigger customers).
DEFAULT_RATE_LIMIT_PER_MIN = 60


class ApiKeyRow(Base):
    __tablename__ = "api_keys"

    id:          Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("api_customers.id"), nullable=False)

    key_hash:   Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    key_prefix: Mapped[str] = mapped_column(String, nullable=False)  # display only, e.g. "mantis_live_ab12cd34"
    label:      Mapped[str | None] = mapped_column(String, nullable=True)

    rate_limit_per_min: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_RATE_LIMIT_PER_MIN
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(tz=timezone.utc),
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at:   Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    customer = relationship("ApiCustomerRow", back_populates="keys")

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None
