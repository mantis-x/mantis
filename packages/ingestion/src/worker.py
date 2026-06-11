"""
Ingestion Worker — entry point.
Run: python -m src.worker

Starts the MantleRPCCollector and writes decoded events
to stdout (and eventually to Postgres).

Phase 1 (this week): logs events to stdout + Redis list.
Phase 2 (next week): persists to Postgres raw_events table.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

from dotenv import load_dotenv

# Load .env from project root (two levels up from packages/ingestion)
load_dotenv(dotenv_path=os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".env"
))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

log = logging.getLogger("mantis.ingestion")

from src.collectors.mantle_rpc import MantleRPCCollector


async def handle_events(events: list) -> None:
    """
    Process a batch of NormalisedEvent objects.
    Writes to stdout as JSON (pipe-friendly) and optionally Redis.
    """
    redis_url = os.getenv("REDIS_URL")
    redis_client = None

    if redis_url:
        try:
            import redis.asyncio as aioredis
            redis_client = aioredis.from_url(redis_url)
        except Exception as e:
            log.warning("Redis unavailable: %s — events will log only", e)

    for event in events:
        payload = {
            "block":    event.block_number,
            "tx":       event.tx_hash[:12] + "...",
            "protocol": event.protocol.value,
            "pool":     event.pool_address[:12] + "...",
            "wallet":   event.wallet_address[:12] + "...",
            "type":     event.event_type.value,
            "usd":      event.amount_usd,
            "ts":       event.timestamp.isoformat(),
        }

        log.info("✓ %s", json.dumps(payload))

        if redis_client:
            try:
                await redis_client.lpush(
                    "mantis:raw_events",
                    json.dumps({
                        **payload,
                        "tx_full":     event.tx_hash,
                        "pool_full":   event.pool_address,
                        "wallet_full": event.wallet_address,
                    })
                )
                # Keep last 10K events in Redis
                await redis_client.ltrim("mantis:raw_events", 0, 9999)
            except Exception as e:
                log.warning("Redis write failed: %s", e)


async def main() -> None:
    log.info("=" * 50)
    log.info("  Mantis Scout — Ingestion Worker")
    log.info("  Mantle DeFi event monitor")
    log.info("=" * 50)

    rpc_url = os.getenv("MANTLE_RPC_URL", "https://rpc.mantle.xyz")
    poll_interval = int(os.getenv("POLL_INTERVAL_SECONDS", "15"))

    log.info("RPC: %s", rpc_url)
    log.info("Poll interval: %ds", poll_interval)

    try:
        collector = MantleRPCCollector(
            rpc_url=rpc_url,
            on_events=handle_events,
            poll_interval=poll_interval,
        )
    except ConnectionError as e:
        log.error("Failed to connect: %s", e)
        sys.exit(1)

    log.info("Collector started — watching for smart money...")
    await collector.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Stopped by user")
