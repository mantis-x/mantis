"""
Detector — main entry point for the detection package.

Consumes NormalisedEvent dicts from Redis (written by the ingestion worker),
runs z-score detection and wallet clustering, then pushes AnomalyCandidate
dicts to a Redis output queue for the enrichment worker.

Redis keys:
  INPUT:  mantis:raw_events        (ingestion worker writes here)
  OUTPUT: mantis:anomaly_candidates (enrichment worker reads here)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".env"
))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mantis.detection")

from src.algorithms.zscore    import ZScoreDetector
from src.algorithms.clustering import WalletClusterer
from src.baselines.pool_baseline import BaselineStore
from src.models.candidate import AnomalyCandidate

# ── Bootstrap with synthetic historical data so detector scores immediately ──
# In production this comes from Postgres. For Week 2 we seed with realistic
# Mantle pool volume estimates so z-scores work from the first real event.
SYNTHETIC_HISTORY = [
    # 7 days × 24h of hourly observations per major pool
    # Format: {pool_address, event_type, amount_usd, timestamp}
]

def _build_synthetic_history() -> list:
    """
    Generate 7 days of synthetic baseline data for all enabled chains.

    Volume estimates:
      Mantle — Agni Finance USDT/WMNT ~$50K/hour, Merchant Moe ~$20K/hour
      Arbitrum — Uniswap V3 WETH/USDC ~$3M/hour (100× Mantle scale)

    Separate baselines per chain prevent Arbitrum volume from masking Mantle
    anomalies and vice versa.
    """
    import random
    random.seed(42)
    now = time.time()
    history = []

    # (chain, pool_address, mean_usd/hr, std_usd/hr)
    pools = [
        # Mantle pools
        ("mantle", "0xcda86a272531e8640cd7f1a92c01839911b90bb0", 50_000,  15_000),
        ("mantle", "0xe6829d9a7ee3040e1276fa75293bde931859e8fa", 30_000,  10_000),
        ("mantle", "0x8e4bcaabb5df13c2c6d8fd44c7e0a5fc9c41e14d", 20_000,   8_000),
        # Arbitrum Uniswap V3 pools — order-of-magnitude higher volume
        ("arbitrum", "0xc6962004f452be9203591991d15f6b388e09e8d0", 3_000_000, 800_000),
        ("arbitrum", "0xc473e2aee3441bf9240be85eb122abb059a3b57c", 1_500_000, 400_000),
        ("arbitrum", "0x641c00a822e8b671738d32a431a4fb6074e5c79d",   800_000, 250_000),
        ("arbitrum", "0x2f5e87c9312fa29aed5c179e456625d79015299c",   400_000, 150_000),
    ]

    for chain, pool, mean_usd, std_usd in pools:
        for hour in range(7 * 24):
            ts = now - (7 * 24 * 3600) + (hour * 3600)
            for etype in ("swap", "mint"):
                vol = max(0, random.gauss(mean_usd, std_usd))
                history.append({
                    "chain":        chain,
                    "pool_address": pool,
                    "event_type":   etype,
                    "amount_usd":   vol,
                    "timestamp":    ts,
                })
    return history


class Detector:
    def __init__(self, redis_url: str = ""):
        self._redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._store      = BaselineStore()
        self._scorer     = ZScoreDetector(self._store)
        self._clusterer  = WalletClusterer()
        self._processed  = 0
        self._candidates = 0

    async def run(self) -> None:
        log.info("=" * 50)
        log.info("  Mantis Scout — Detection Engine")
        log.info("  Z-score anomaly detector + wallet clusterer")
        log.info("=" * 50)

        # Seed baselines
        log.info("Seeding baselines from synthetic history...")
        history = _build_synthetic_history()
        self._store.seed_from_historical(history)
        log.info(
            "Baselines ready — %d pools, threshold=%.1f",
            self._store.pool_count(), float(os.getenv("ZSCORE_THRESHOLD", "2.5"))
        )

        import redis.asyncio as aioredis
        r = aioredis.from_url(self._redis_url, decode_responses=True)

        log.info("Listening on Redis mantis:raw_events ...")

        while True:
            try:
                # Blocking pop — wait up to 5s for new event
                item = await r.brpop("mantis:raw_events", timeout=5)
                if item is None:
                    continue

                _, payload = item
                event_dict = json.loads(payload)
                await self._process(event_dict, r)

            except Exception as exc:
                log.warning("Detection error: %s", exc)
                await asyncio.sleep(1)

    async def _process(self, event_dict: dict, r) -> None:
        """Score one event and emit a candidate if anomalous."""
        self._processed += 1

        # Reconstruct a minimal event object from the dict
        event = _DictEvent(event_dict)
        scored = self._scorer.score_event(event)

        if scored is None:
            return

        cluster = self._clusterer._solo_cluster(scored)
        candidate = AnomalyCandidate(
            cluster    = cluster,
            raw_events = [scored],
        )

        payload = json.dumps(candidate.to_dict())
        await r.lpush("mantis:anomaly_candidates", payload)
        await r.ltrim("mantis:anomaly_candidates", 0, 999)

        self._candidates += 1
        log.info(
            "🎯 Candidate queued: z=%.2f protocol=%s type=%s usd=%.0f",
            scored.z_score, scored.protocol, scored.event_type, scored.amount_usd,
        )

        # Log stats every 100 events
        if self._processed % 100 == 0:
            log.info("Stats: %s", self._scorer.stats)


class _DictEvent:
    """Minimal event wrapper around a raw dict from Redis."""
    def __init__(self, d: dict):
        self.chain          = d.get("chain", "mantle")   # default for pre-refactor payloads
        self.block_number   = d.get("block", 0)
        self.tx_hash        = d.get("tx_full", d.get("tx", ""))
        self.protocol       = _Val(d.get("protocol", ""))
        self.pool_address   = d.get("pool_full", d.get("pool", ""))
        self.wallet_address = d.get("wallet_full", d.get("wallet", ""))
        self.event_type     = _Val(d.get("type", "swap"))
        self.amount_usd     = float(d.get("usd", 0))
        ts_str              = d.get("ts", "")
        try:
            self.timestamp  = datetime.fromisoformat(ts_str)
        except Exception:
            self.timestamp  = datetime.now(tz=timezone.utc)


class _Val:
    """Wraps a string so .value returns the string (matches enum interface)."""
    def __init__(self, s: str):
        self.value = s
    def __str__(self): return self.value


async def main():
    detector = Detector()
    await detector.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Detection stopped")
