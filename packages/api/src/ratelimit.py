"""Per-key rate limiting — a fixed-window counter in Redis.

Fixed window (per key, per calendar minute) is deliberately simple and
race-safe via atomic INCR: the first request in a window sets a 60s TTL, and
any request past the key's limit within that window is rejected. Redis is
already required infra for every worker in this system, so no new dependency.
Fails OPEN (allows the request) on a Redis error — a monitoring outage should
degrade to "no rate limiting", never to "API down".
"""
from __future__ import annotations

import logging
import time

import redis

from src.config import REDIS_URL

log = logging.getLogger("mantis.api.ratelimit")

_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(REDIS_URL, decode_responses=True)
    return _client


def client() -> redis.Redis:
    """Shared Redis client (also honors the fakeredis injected in tests)."""
    return _redis()


def check_rate_limit_key(key: str, limit: int, window_s: int = 60) -> bool:
    """Fixed-window rate limit for an arbitrary string key (per-API-key, per-IP,
    …). Returns True if allowed. Fails open on any Redis error."""
    if limit <= 0:
        return True
    window = int(time.time()) // window_s
    redis_key = f"mantis:api:ratelimit:{key}:{window}"
    try:
        r = _redis()
        count = r.incr(redis_key)
        if count == 1:
            r.expire(redis_key, window_s)
        return count <= limit
    except Exception as exc:  # noqa: BLE001 — fail open, never take the API down
        log.warning("Rate-limit check failed (allowing request): %s", exc)
        return True


def check_rate_limit(key_id: int, limit_per_min: int) -> bool:
    """Per-API-key per-minute limit (used by the auth dependency)."""
    return check_rate_limit_key(str(key_id), limit_per_min, window_s=60)


def reset_client_for_tests() -> None:
    global _client
    _client = None
