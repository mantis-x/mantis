"""Authentication + gating for the api worker.

`require_api_key` is a FastAPI dependency (sync `def`, so it runs in the
threadpool and composes with the repo's sync SQLAlchemy session layer). It:
  1. 404s every authenticated route when API_TIER_ENABLED is false (don't
     advertise a surface that isn't sold yet).
  2. Extracts the Bearer key, hashes it, looks it up by key_hash.
  3. Rejects a missing/revoked key or a lapsed customer (401 / 403).
  4. Enforces the key's per-minute rate limit (429).
  5. Bumps last_used_at (best-effort) and returns a session-free context.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.config import api_tier_enabled
from src.db.connection import get_session
from src.db.models.api_key import ApiKeyRow
from src.ratelimit import check_rate_limit
from src.security.api_keys import hash_api_key

_bearer = HTTPBearer(auto_error=False)


@dataclass
class ApiKeyContext:
    """Session-free snapshot of the authenticated caller."""
    key_id: int
    customer_id: int
    customer_label: str
    rate_limit_per_min: int


def require_api_key(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> ApiKeyContext:
    # 1. Gate: the tier isn't live → the surface doesn't exist.
    if not api_tier_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    # 2. Must present a Bearer credential.
    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    key_hash = hash_api_key(creds.credentials)

    with get_session() as session:
        row: ApiKeyRow | None = (
            session.query(ApiKeyRow).filter(ApiKeyRow.key_hash == key_hash).first()
        )
        if row is None or row.is_revoked:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or revoked API key",
                headers={"WWW-Authenticate": "Bearer"},
            )

        customer = row.customer
        if customer is None or not customer.is_active():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="API tier not active for this customer (expired or missing)",
            )

        # 4. Rate limit per key.
        if not check_rate_limit(row.id, row.rate_limit_per_min):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
                headers={"Retry-After": "60"},
            )

        # 5. Best-effort usage stamp; snapshot everything before the session closes.
        row.last_used_at = datetime.now(tz=timezone.utc)
        ctx = ApiKeyContext(
            key_id=row.id,
            customer_id=customer.id,
            customer_label=customer.label,
            rate_limit_per_min=row.rate_limit_per_min,
        )
    return ctx
