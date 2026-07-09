"""
Enrichment Worker — entry point.
Run: python -m src.worker (from packages/enrichment/)

Consumes anomaly candidates from Redis,
calls Claude Sonnet for classification,
pushes qualified signals to the delivery queue.

Redis keys:
  INPUT:   mantis:anomaly_candidates  (detection worker writes here)
  OUTPUT:  mantis:signals             (delivery worker reads here — rolling
                                       display log, capped at 1000, consumed
                                       via BRPOP)
  OUTPUT:  mantis:signals:exec        (executor worker reads here — a
                                       separate FIFO queue so execution
                                       and delivery each get their own
                                       exclusive copy of every signal;
                                       consuming one must never remove a
                                       signal the other hasn't seen yet)
  OUTPUT:  mantis:signals:tracking    (tracking worker reads here — feeds
                                       the durable Postgres signals/
                                       signal_outcomes tables used for the
                                       track record / backtest reports)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".env"
))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mantis.enrichment")

from src.enricher import Enricher


async def main() -> None:
    log.info("=" * 50)
    log.info("  Mantis Scout — Enrichment Worker")
    log.info("  Claude Sonnet signal classifier")
    log.info("=" * 50)

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    try:
        enricher = Enricher()
        log.info("Enricher ready — model=claude-sonnet-5")
    except ValueError as e:
        log.error("%s", e)
        return

    import redis.asyncio as aioredis
    r = aioredis.from_url(redis_url, decode_responses=True)

    log.info("Listening on Redis mantis:anomaly_candidates ...")

    processed = 0
    while True:
        try:
            item = await r.brpop("mantis:anomaly_candidates", timeout=5)
            if item is None:
                continue

            _, payload = item
            candidate  = json.loads(payload)

            log.info(
                "Processing candidate: protocol=%s z=%.2f vol=$%.0f",
                candidate.get("protocol", "?"),
                float(candidate.get("z_score", 0)),
                float(candidate.get("total_volume_usd", 0)),
            )

            signal = enricher.enrich(candidate)

            if signal:
                signal_payload = json.dumps(signal.to_dict())

                # Delivery's rolling display log — capped, consumed via BRPOP
                await r.lpush("mantis:signals", signal_payload)
                await r.ltrim("mantis:signals", 0, 999)

                # Executor's independent FIFO queue — separate list so a
                # slow/down executor can never cause delivery to skip a
                # signal, and vice versa. Capped as a backpressure valve:
                # if the executor is down long enough to queue 5000 signals,
                # further arrivals are dropped rather than growing Redis
                # memory unboundedly.
                await r.rpush("mantis:signals:exec", signal_payload)
                await r.ltrim("mantis:signals:exec", 0, 4999)

                # Tracking's independent FIFO queue — same reasoning as
                # mantis:signals:exec above.
                await r.rpush("mantis:signals:tracking", signal_payload)
                await r.ltrim("mantis:signals:tracking", 0, 4999)

                log.info(
                    "📤 Signal queued for delivery + execution + tracking: %s confidence=%d",
                    signal.signal_type.value, signal.confidence,
                )

            processed += 1
            if processed % 10 == 0:
                log.info("Enrichment stats: %s", enricher.stats)

        except KeyboardInterrupt:
            break
        except Exception as exc:
            log.warning("Enrichment error: %s", exc)
            await asyncio.sleep(1)

    log.info("Final stats: %s", enricher.stats)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Enrichment worker stopped")
