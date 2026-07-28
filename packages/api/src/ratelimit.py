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


def check_rate_limit(key_id: int, limit_per_min: int) -> bool:
    """Return True if the request is allowed, False if the per-minute limit is
    exceeded. Fails open on any Redis error."""
    if limit_per_min <= 0:
        return True
    window = int(time.time()) // 60
    redis_key = f"mantis:api:ratelimit:{key_id}:{window}"
    try:
        r = _redis()
        count = r.incr(redis_key)
        if count == 1:
            r.expire(redis_key, 60)
        return count <= limit_per_min
    except Exception as exc:  # noqa: BLE001 — fail open, never take the API down
        log.warning("Rate-limit check failed (allowing request): %s", exc)
        return True


def reset_client_for_tests() -> None:
    global _client
    _client = None
