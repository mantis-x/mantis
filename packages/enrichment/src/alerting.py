"""
alerting.py — tracks consecutive enrichment failures and raises the alarm
loudly instead of the historical silent-failure pattern: three separate
real bugs (retired model, exhausted Anthropic billing, a ThinkingBlock
crash from the Sonnet 5 migration) each went unnoticed for weeks because
`_call_claude()`'s error handlers only ever logged and returned None.

Two layers, deliberately redundant so one missing piece of config doesn't
mean total silence again:

  1. Always-on — publishes consecutive/total failure counters to Redis
     (mantis:stats:enrichment_errors_*) so /status can surface "enrichment
     degraded" with zero extra configuration.
  2. Optional — if TELEGRAM_BOT_TOKEN and ADMIN_TELEGRAM_CHAT_ID are both
     set, pushes a direct Telegram message once the consecutive-failure
     streak crosses ENRICHMENT_ALERT_THRESHOLD, rate-limited by a Redis-TTL
     cooldown lock so a sustained outage sends one alert per window, not
     one per failed call.
"""
from __future__ import annotations

import asyncio
import logging
import os

import requests

log = logging.getLogger(__name__)

ALERT_THRESHOLD  = int(os.getenv("ENRICHMENT_ALERT_THRESHOLD", "5"))
ALERT_COOLDOWN_S = int(os.getenv("ENRICHMENT_ALERT_COOLDOWN_MINUTES", "30")) * 60

CONSECUTIVE_KEY = "mantis:stats:enrichment_errors_consecutive"
TOTAL_KEY       = "mantis:stats:enrichment_errors_total"
COOLDOWN_KEY    = "mantis:stats:enrichment_alert_sent"


class EnrichmentAlerter:
    """One instance per worker process — call record() after every enrich()."""

    def __init__(self):
        self._consecutive = 0

    async def record(self, r, is_error: bool) -> None:
        """
        Update counters after one Enricher.enrich() call. is_error must
        reflect a real Claude-call/parse failure, not a legitimate
        low-confidence discard — callers should compare
        enricher.stats["errors"] before/after, not just `signal is None`.
        """
        self._consecutive = self._consecutive + 1 if is_error else 0

        try:
            await r.set(CONSECUTIVE_KEY, self._consecutive)
            if is_error:
                await r.incr(TOTAL_KEY)
            else:
                # Recovered — clear the cooldown so a fresh alert can fire
                # if it breaks again later, rather than staying suppressed
                # for the rest of the cooldown window from the last incident.
                await r.delete(COOLDOWN_KEY)
        except Exception as exc:
            log.debug("Could not publish enrichment health to Redis: %s", exc)

        if self._consecutive >= ALERT_THRESHOLD:
            await self._maybe_alert(r)

    async def _maybe_alert(self, r) -> None:
        try:
            # Atomic "only one alert per cooldown window" lock.
            acquired = await r.set(COOLDOWN_KEY, "1", nx=True, ex=ALERT_COOLDOWN_S)
        except Exception as exc:
            log.debug("Could not acquire alert cooldown lock: %s", exc)
            acquired = True  # fail open — better to over-alert than stay silent

        if not acquired:
            return

        msg = (
            f"🔴 Mantis enrichment degraded — {self._consecutive} consecutive "
            f"Claude API failures. Check ANTHROPIC_API_KEY billing/validity "
            f"and recent enrichment worker logs."
        )
        log.error(msg)
        await _send_telegram_alert(msg)


async def _send_telegram_alert(text: str) -> None:
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("ADMIN_TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        log.warning(
            "Enrichment alert threshold crossed but TELEGRAM_BOT_TOKEN/"
            "ADMIN_TELEGRAM_CHAT_ID aren't both set — alert only visible "
            "via Redis counters / logs, not pushed anywhere. Set "
            "ADMIN_TELEGRAM_CHAT_ID to enable the push alert."
        )
        return
    try:
        await asyncio.to_thread(
            requests.post,
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception as exc:
        log.warning("Failed to push admin alert to Telegram: %s", exc)
