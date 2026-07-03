"""
Ingestion Worker — entry point.
Run: python -m src.worker

Starts one ChainCollector per enabled chain (CHAINS env var) and writes
decoded events to stdout + Redis. Chains run concurrently via asyncio.gather().

Phase 0: CHAINS=mantle (default) — identical behaviour to before refactor.
Phase 1: CHAINS=mantle,arbitrum — two collectors, one Redis queue, chain field on every payload.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".env"
))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

log = logging.getLogger("mantis.ingestion")

from src.chains import get_enabled_chains
from src.collectors.evm_rpc import ChainCollector


def make_handler(redis_url: str):
    """Return an async event handler that writes to stdout + Redis."""

    async def handle_events(events: list) -> None:
        redis_client = None
        if redis_url:
            try:
                import redis.asyncio as aioredis
                redis_client = aioredis.from_url(redis_url)
            except Exception as e:
                log.warning("Redis unavailable: %s — events will log only", e)

        for event in events:
            payload = {
                "chain":    event.chain,
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
                    await redis_client.ltrim("mantis:raw_events", 0, 9999)
                except Exception as e:
                    log.warning("Redis write failed: %s", e)

    return handle_events


async def main() -> None:
    log.info("=" * 50)
    log.info("  Mantis Scout — Ingestion Worker")
    log.info("  Multi-chain DeFi event monitor")
    log.info("=" * 50)

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    chains    = get_enabled_chains()

    log.info("Enabled chains: %s", [c.name for c in chains])

    handler     = make_handler(redis_url)
    collectors  = []
    for config in chains:
        log.info("Connecting to %s — RPC: %s", config.name, config.rpc_url())
        try:
            collector = ChainCollector(config=config, on_events=handler)
            collectors.append(collector)
        except ConnectionError as e:
            log.error("Failed to connect to %s: %s", config.name, e)
            if len(chains) == 1:
                sys.exit(1)
            log.warning("Skipping %s — continuing with remaining chains", config.name)

    if not collectors:
        log.error("No chains connected — exiting")
        sys.exit(1)

    log.info("Starting %d collector(s)...", len(collectors))
    await asyncio.gather(*[c.run() for c in collectors])


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Stopped by user")
