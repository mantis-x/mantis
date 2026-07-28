"""
Webhook dispatcher (API tier, Phase B). Run: python -m src.webhook_dispatcher

Consumes mantis:signals:api (fanned out from the tracking worker AFTER a signal
is persisted, so the payload carries the durable DB id), and POSTs each signal
to every active webhook whose filters match — HMAC-signed, with bounded-
concurrency sends (a slow/dead endpoint can't head-of-line-block the rest),
per-request timeout, exponential-backoff retries, and consecutive-failure
auto-disable (streak resets on any success — same pattern as EnrichmentAlerter).

Delivery is idempotent per (webhook, signal) via the unique constraint on
webhook_deliveries: re-reading the same signal never double-delivers.

Inert until API_TIER_ENABLED and a customer has registered a webhook — with no
active webhooks it just drains the queue doing nothing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mantis.api.webhooks")

from src.config import REDIS_URL
from src.db.connection import get_session
from src.db.models.webhook import WebhookRow
from src.db.models.webhook_delivery import DeliveryStatus, WebhookDeliveryRow
from src.security.webhook_signing import SIG_HEADER, TS_HEADER, sign

MAX_ATTEMPTS       = int(os.getenv("WEBHOOK_MAX_ATTEMPTS", "5"))
DISABLE_THRESHOLD  = int(os.getenv("WEBHOOK_DISABLE_THRESHOLD", "10"))   # consecutive exhausted deliveries
TIMEOUT_S          = float(os.getenv("WEBHOOK_TIMEOUT_S", "10"))
SEND_CONCURRENCY   = int(os.getenv("WEBHOOK_SEND_CONCURRENCY", "10"))
RETRY_SWEEP_S      = int(os.getenv("WEBHOOK_RETRY_SWEEP_S", "60"))
# Backoff in minutes, indexed by (attempts-1); last value repeats.
BACKOFF_MINUTES    = [1, 2, 4, 8, 16]


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _backoff(attempts: int) -> timedelta:
    return timedelta(minutes=BACKOFF_MINUTES[min(attempts - 1, len(BACKOFF_MINUTES) - 1)])


def matches(event_filters: dict | None, sig: dict) -> bool:
    """True if a signal passes a webhook's optional filters."""
    if not event_filters:
        return True
    if event_filters.get("chain") and sig.get("chain") != event_filters["chain"]:
        return False
    if event_filters.get("signal_type") and sig.get("signal_type") != event_filters["signal_type"]:
        return False
    minc = event_filters.get("min_confidence")
    if minc is not None and sig.get("confidence", 0) < minc:
        return False
    return True


async def _default_sender(url: str, headers: dict, body: str) -> tuple[bool, int | None]:
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=TIMEOUT_S)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.post(url, data=body, headers=headers) as resp:
                return (200 <= resp.status < 300, resp.status)
    except Exception as exc:  # noqa: BLE001 — any failure is a delivery failure
        log.debug("webhook POST failed: %s", exc)
        return (False, None)


class WebhookDispatcher:
    def __init__(self, sender=None):
        # sender: async (url, headers, body) -> (ok, status_code|None). Injectable for tests.
        self._sender = sender or _default_sender
        self._sem = asyncio.Semaphore(SEND_CONCURRENCY)

    def _headers(self, secret: str, body: str, signal_id: int) -> dict:
        ts, sig = sign(secret, body)
        return {
            "Content-Type": "application/json",
            TS_HEADER: ts,
            SIG_HEADER: sig,
            "X-Mantis-Signal-Id": str(signal_id),
        }

    async def _send_one(self, url: str, headers: dict, body: str) -> tuple[bool, int | None]:
        async with self._sem:
            return await self._sender(url, headers, body)

    def _apply_result(self, delivery: WebhookDeliveryRow, webhook: WebhookRow,
                      ok: bool, status: int | None) -> None:
        now = _now()
        delivery.attempts += 1
        delivery.last_attempt_at = now
        delivery.response_code = status
        if ok:
            delivery.status = DeliveryStatus.DELIVERED
            delivery.next_retry_at = None
            webhook.consecutive_failures = 0          # streak resets on any success
        elif delivery.attempts >= MAX_ATTEMPTS:
            delivery.status = DeliveryStatus.EXHAUSTED
            delivery.next_retry_at = None
            webhook.consecutive_failures += 1
            if webhook.consecutive_failures >= DISABLE_THRESHOLD:
                webhook.active = False
                webhook.disabled_at = now
                log.warning("Webhook %s auto-disabled after %d consecutive failures",
                            webhook.id, webhook.consecutive_failures)
        else:
            delivery.status = DeliveryStatus.FAILED
            delivery.next_retry_at = now + _backoff(delivery.attempts)

    async def deliver_signal(self, signal_dict: dict) -> int:
        """Fan a freshly-persisted signal to all matching active webhooks.
        Returns the number of endpoints attempted. Idempotent per (webhook, signal)."""
        signal_id = signal_dict.get("id")
        if signal_id is None:
            log.warning("signal has no id — cannot deliver (fan-out must be post-persist)")
            return 0

        body = json.dumps(signal_dict, sort_keys=True)

        # Phase 1 (sync DB): pick matching active webhooks, create pending delivery
        # rows idempotently, collect what to send.
        to_send = []   # (webhook_id, delivery_id, url, headers)
        with get_session() as session:
            webhooks = session.query(WebhookRow).filter(WebhookRow.active.is_(True)).all()
            for wh in webhooks:
                if not matches(wh.event_filters, signal_dict):
                    continue
                existing = (
                    session.query(WebhookDeliveryRow)
                    .filter(WebhookDeliveryRow.webhook_id == wh.id,
                            WebhookDeliveryRow.signal_id == signal_id)
                    .first()
                )
                if existing is not None:
                    continue   # already handled (idempotent)
                delivery = WebhookDeliveryRow(
                    webhook_id=wh.id, signal_id=signal_id, status=DeliveryStatus.PENDING
                )
                session.add(delivery)
                session.flush()
                to_send.append((wh.id, delivery.id, wh.url, self._headers(wh.secret, body, signal_id)))

        if not to_send:
            return 0

        # Phase 2 (async): send all concurrently, bounded + per-request timeout.
        results = await asyncio.gather(
            *[self._send_one(url, headers, body) for (_wid, _did, url, headers) in to_send]
        )

        # Phase 3 (sync DB): apply outcomes.
        with get_session() as session:
            for (wid, did, _url, _h), (ok, status) in zip(to_send, results):
                delivery = session.get(WebhookDeliveryRow, did)
                webhook = session.get(WebhookRow, wid)
                if delivery and webhook:
                    self._apply_result(delivery, webhook, ok, status)
        return len(to_send)

    async def retry_due(self) -> int:
        """Re-attempt failed deliveries whose backoff has elapsed. Returns count retried."""
        now = _now()
        with get_session() as session:
            due = (
                session.query(WebhookDeliveryRow)
                .filter(WebhookDeliveryRow.status == DeliveryStatus.FAILED,
                        WebhookDeliveryRow.next_retry_at <= now)
                .all()
            )
            batch = []
            for d in due:
                wh = session.get(WebhookRow, d.webhook_id)
                if wh is None or not wh.active:
                    continue
                sig = _signal_body_for_retry(session, d.signal_id)
                if sig is None:
                    continue
                batch.append((d.id, wh.id, wh.url, wh.secret, sig))

        if not batch:
            return 0

        results = []
        for (did, wid, url, secret, sig_body) in batch:
            headers = self._headers(secret, sig_body, _extract_id(sig_body))
            results.append(await self._send_one(url, headers, sig_body))

        with get_session() as session:
            for (did, wid, _u, _s, _b), (ok, status) in zip(batch, results):
                delivery = session.get(WebhookDeliveryRow, did)
                webhook = session.get(WebhookRow, wid)
                if delivery and webhook:
                    self._apply_result(delivery, webhook, ok, status)
        return len(batch)

    async def run(self) -> None:
        import redis.asyncio as aioredis

        log.info("=" * 55)
        log.info("  Mantis API — Webhook Dispatcher (Phase B)")
        log.info("=" * 55)
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        log.info("Listening on Redis mantis:signals:api ...")
        last_retry = 0.0
        while True:
            try:
                item = await r.blpop("mantis:signals:api", timeout=5)
                if item is not None:
                    _, raw = item
                    n = await self.deliver_signal(json.loads(raw))
                    if n:
                        log.info("Dispatched signal to %d webhook(s)", n)
                now = time.time()
                if now - last_retry >= RETRY_SWEEP_S:
                    retried = await self.retry_due()
                    if retried:
                        log.info("Retried %d due webhook delivery(ies)", retried)
                    last_retry = now
            except Exception as exc:  # noqa: BLE001
                log.warning("Webhook dispatcher error: %s", exc)
                await asyncio.sleep(1)


def _signal_body_for_retry(session, signal_id: int) -> str | None:
    """Rebuild the exact signed body for a retry from the durable signal row."""
    from src.db.models.signal import SignalRow

    row = session.get(SignalRow, signal_id)
    if row is None:
        return None
    return json.dumps(row.to_dict(), sort_keys=True)


def _extract_id(body: str) -> int:
    try:
        return int(json.loads(body).get("id", 0))
    except Exception:  # noqa: BLE001
        return 0


def main() -> None:
    asyncio.run(WebhookDispatcher().run())


if __name__ == "__main__":
    main()
