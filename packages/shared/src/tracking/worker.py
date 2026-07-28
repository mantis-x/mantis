"""
Tracking Worker — entry point for the track record / backtest instrumentation.
Run: python -m src.tracking.worker (from packages/shared/)

Listens on Redis mantis:signals:tracking (see packages/enrichment/src/worker.py
for the producer side), persists every signal to Postgres, and schedules
price checks at fixed horizons (1h/4h/24h/7d). On the same loop, periodically
sweeps for due checks and records the % price move — this is the durable
data scripts/backtest_signals.py reads to produce a track record.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..", ".env"
))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mantis.tracking")

from src.db.connection import get_session
from src.tracking.signal_outcome_tracker import SignalOutcomeTracker
from src.billing.pro_payment_watcher import ProPaymentWatcher, sweep_expired_pro
from src.billing.api_payment_watcher import ApiPaymentWatcher

# How often to sweep for due outcome checks, independent of signal arrival rate.
DUE_CHECK_INTERVAL_S = int(os.getenv("TRACKING_DUE_CHECK_INTERVAL_S", "300"))
# Pro-tier payment/expiry sweep — reuses the same due-check cadence as outcomes.
PRO_TIER_CHECK_INTERVAL_S = int(os.getenv("PRO_TIER_CHECK_INTERVAL_S", "300"))


async def main() -> None:
    log.info("=" * 55)
    log.info("  Mantis Tracking — Signal Outcome / Backtest Instrumentation")
    log.info("=" * 55)

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    import redis as sync_redis
    import redis.asyncio as aioredis
    # Sync client for wallet track-record writes inside check_due_outcomes
    # (already a blocking call alongside sync Postgres) — separate from the
    # async client below, which only serves the mantis:signals:tracking queue.
    wallet_redis = sync_redis.from_url(redis_url, decode_responses=True)
    tracker = SignalOutcomeTracker(redis_client=wallet_redis)
    pro_watcher = ProPaymentWatcher(redis_client=wallet_redis)
    api_watcher = ApiPaymentWatcher(redis_client=wallet_redis)

    r = aioredis.from_url(redis_url, decode_responses=True)

    log.info("Listening on Redis mantis:signals:tracking ...")

    processed = 0
    last_due_check = 0.0
    last_pro_check = 0.0

    while True:
        try:
            item = await r.blpop("mantis:signals:tracking", timeout=5)
            if item is not None:
                _, raw = item
                signal_dict = json.loads(raw)

                with get_session() as session:
                    signal_row = tracker.persist_signal(session, signal_dict)
                    tracker.schedule_outcomes(session, signal_row)
                    # Capture the durable-id payload while the session is open;
                    # expire_on_commit=False keeps attributes readable after.
                    api_payload = json.dumps(signal_row.to_dict())

                # Fan out to the API-tier webhook queue AFTER persist committed —
                # this is the only stage where the signal has its durable DB id,
                # which the webhook dispatcher needs to (a) let customers correlate
                # a delivery back to GET /v1/signals/{id} and (b) key idempotent
                # delivery rows. Capped like the other fan-out queues; a webhook
                # dispatcher that's down just misses the backlog, never blocks
                # tracking. No-op in practice until API_TIER_ENABLED + a webhook exists.
                await r.rpush("mantis:signals:api", api_payload)
                await r.ltrim("mantis:signals:api", 0, 4999)

                processed += 1
                log.info(
                    "Tracked signal: id=%s chain=%s protocol=%s type=%s conf=%d",
                    signal_row.id, signal_dict.get("chain"), signal_dict.get("protocol"),
                    signal_dict.get("signal_type"), signal_dict.get("confidence", 0),
                )

            now = time.time()
            if now - last_due_check >= DUE_CHECK_INTERVAL_S:
                with get_session() as session:
                    checked = tracker.check_due_outcomes(session)
                if checked:
                    log.info("Checked %d due outcome(s)", checked)
                last_due_check = now

            if now - last_pro_check >= PRO_TIER_CHECK_INTERVAL_S:
                with get_session() as session:
                    credited = pro_watcher.check_new_payments(session)
                    expired = sweep_expired_pro(session)
                # Only advance the scan cursor once the above has actually
                # committed — see check_new_payments' docstring for why.
                pro_watcher.commit_cursor()
                if credited:
                    log.info("Pro tier: credited %d payment(s)", credited)
                if expired:
                    log.info("Pro tier: %d subscription(s) expired", expired)

                # API-tier billing — same cadence, same cursor-after-commit
                # discipline. Scans a DIFFERENT receive address than Pro, so the
                # two never collide. No expiry sweep needed: API access is gated
                # live on api_tier_expires_at (ApiCustomerRow.is_active()).
                with get_session() as session:
                    api_credited = api_watcher.check_new_payments(session)
                api_watcher.commit_cursor()
                if api_credited:
                    log.info("API tier: credited %d payment(s)", api_credited)
                last_pro_check = now

            if processed and processed % 20 == 0:
                log.info("Tracking stats: signals_persisted=%d", processed)

        except KeyboardInterrupt:
            break
        except Exception as exc:
            log.warning("Tracking worker error: %s", exc)
            await asyncio.sleep(2)

    log.info("Tracking worker stopped — processed=%d", processed)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Mantis Tracking stopped")
