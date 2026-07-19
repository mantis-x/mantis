"""
Best-effort live stat reads from Redis, published by other workers.

The detection worker (packages/detection) is a separate process and the
only source of truth for how many anomaly candidates it has scored. It
publishes a running counter to mantis:stats:candidates on every candidate;
this module gives the delivery bots a way to read that counter for /status
without needing a persistent cross-process connection or a shared library.

Every read opens a short-lived connection and fails soft (returns None)
on any error — a /status command should never break because a stats read
timed out.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

CANDIDATES_KEY           = "mantis:stats:candidates"
ENRICHMENT_ERRORS_KEY    = "mantis:stats:enrichment_errors_consecutive"


async def get_live_candidates(redis_url: str) -> int | None:
    """Read the detection worker's live candidate counter, or None on failure."""
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(redis_url, decode_responses=True)
        try:
            val = await r.get(CANDIDATES_KEY)
        finally:
            await r.aclose()
        return int(val) if val is not None else None
    except Exception as exc:
        log.debug("Could not read live candidates stat: %s", exc)
        return None


async def get_enrichment_consecutive_errors(redis_url: str) -> int | None:
    """
    Read the enrichment worker's consecutive-failure streak (see
    packages/enrichment/src/alerting.py), or None on failure. 0 means
    healthy; a subscriber-facing /status uses this to surface "degraded"
    without needing ADMIN_TELEGRAM_CHAT_ID configured for push alerts.
    """
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(redis_url, decode_responses=True)
        try:
            val = await r.get(ENRICHMENT_ERRORS_KEY)
        finally:
            await r.aclose()
        return int(val) if val is not None else None
    except Exception as exc:
        log.debug("Could not read enrichment health stat: %s", exc)
        return None
