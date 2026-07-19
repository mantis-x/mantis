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
from collections import defaultdict
from datetime import datetime, timedelta, timezone

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
from src.algorithms.clustering import WalletClusterer, CLUSTER_WINDOW_MINUTES
from src.baselines.pool_baseline import BaselineStore
from src.models.candidate import AnomalyCandidate, WalletCluster, ScoredEvent

# Cooldown before the same (wallet, pool, event_type) can fire a second solo
# candidate — found 2026-07-19: the same wallet's repeat activity on one pool
# (e.g. baseline recalculating between two mints seconds apart) was producing
# near-duplicate alerts with no suppression at all.
SOLO_COOLDOWN_MINUTES = int(os.getenv("SOLO_SIGNAL_COOLDOWN_MINUTES", "30"))

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
      Mantle — Agni Finance real pools (WMNT/USDC, WMNT/WETH, WMNT/USDT):
        $3-35/hour, from real on-chain swaps observed 2026-07-16. The
        addresses tracked before this date were never real pools (verified
        zero Swap-topic hits, ever) — see chains.py's MANTLE pool_registry
        comment for the full story. Genuinely retail-scale activity today,
        not the $20K-50K/hour previously assumed.
      Arbitrum — Uniswap V3 WETH/USDC ~$3M/hour (~100,000× Mantle's real scale)
      HashKey — token transfer-flow monitoring, not swaps (no DEX with
        meaningful volume found at launch — see chains.py's HASHKEY entry).
        Baseline scale is from real observed on-chain data (2026-07-09):
        USDT/WETH ~$20-60 per transfer at ~7/hour, WHSK smaller and far
        more variable (~$0-120 per transfer at ~2/hour) — genuinely
        small-dollar activity today, not whale/institutional scale, though
        the z-score detector works on relative variance regardless of
        absolute size.

    Separate baselines per chain prevent Arbitrum volume from masking Mantle
    anomalies and vice versa.
    """
    import random
    random.seed(42)
    now = time.time()
    history = []

    # (chain, pool_address, mean_usd/hr, std_usd/hr, event_types)
    pools = [
        # Mantle pools — real Agni Finance V3 pools (fixed 2026-07-16; see
        # chains.py comment for how these were found/verified). Volume is
        # calibrated from real observed swaps (20,000-block/~11h sample),
        # not assumed — genuine Mantle DEX activity here is retail-scale
        # (single-digit to low-double-digit dollars per swap), nowhere near
        # the $20K-50K/hr previously assumed for the wrong addresses this
        # replaced.
        ("mantle", "0x1858d52cf57c07a018171d7a1e68dc081f17144f", 35,  25, ("swap", "mint")),  # WMNT/USDC 0.05%
        ("mantle", "0x54169896d28dec0ffabe3b16f90f71323774949f",  5,   4, ("swap", "mint")),  # WMNT/WETH 0.05%
        ("mantle", "0xd08c50f7e69e9aeb2867deff4a8053d9a855e26a",  3,   3, ("swap", "mint")),  # WMNT/USDT 0.05%
        # Arbitrum Uniswap V3 pools — order-of-magnitude higher volume
        ("arbitrum", "0xc6962004f452be9203591991d15f6b388e09e8d0", 3_000_000, 800_000, ("swap", "mint")),
        ("arbitrum", "0xc473e2aee3441bf9240be85eb122abb059a3b57c", 1_500_000, 400_000, ("swap", "mint")),
        ("arbitrum", "0x641c00a822e8b671738d32a431a4fb6074e5c79d",   800_000, 250_000, ("swap", "mint")),
        ("arbitrum", "0x2f5e87c9312fa29aed5c179e456625d79015299c",   400_000, 150_000, ("swap", "mint")),
        # HashKey Chain — token transfer flows (USDT, WETH, WHSK contracts)
        ("hashkey", "0xf1b50ed67a9e2cc94ad3c477779e2d4cbfff9029",       25,      15, ("transfer",)),
        ("hashkey", "0xefd4bc9afd210517803f293ababd701caeecdfd0",       35,      20, ("transfer",)),
        ("hashkey", "0xb210d2120d57b758ee163cffb43e73728c471cf1",        5,       8, ("transfer",)),
        # Ethereum mainnet — blue-chip Uniswap V3 pools (2026-07-18). Mean/std
        # extrapolated from a real (not assumed) ~20-block/~4min sample of
        # decoded Swap amounts via eth_getLogs — see chains.py's ETHEREUM
        # comment for pool verification. Short sample, so treat as a rough
        # order-of-magnitude calibration, same caveat as Arbitrum's figures.
        ("ethereum", "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640", 1_200_000, 350_000, ("swap", "mint")),  # USDC/WETH 0.05%
        ("ethereum", "0x11b815efb8f581194ae79006d24e0d814b7697f6",   250_000,  70_000, ("swap", "mint")),  # WETH/USDT 0.05%
        ("ethereum", "0x4585fe77225b41b697c938b018e2ac67ac5a20c0",    80_000,  25_000, ("swap", "mint")),  # WBTC/WETH 0.05%
    ]

    for chain, pool, mean_usd, std_usd, event_types in pools:
        for hour in range(7 * 24):
            ts = now - (7 * 24 * 3600) + (hour * 3600)
            for etype in event_types:
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

        # Real multi-wallet clustering support — previously WalletClusterer.cluster()
        # existed and worked but was never wired into the live streaming path (see
        # PROJECT_STATE.md Known Bugs: "No real multi-wallet clustering"). _process()
        # only ever called _solo_cluster() on one event at a time, so every candidate
        # was a single-wallet cluster and enrichment's own calibration table capped
        # confidence at 55-69 regardless of z-score/volume. Fixed by keeping a short
        # rolling buffer of recently-flagged events per (pool, event_type) and running
        # the real cluster() over it on every new event, in *addition* to the existing
        # solo emission (not instead of it) — purely additive, so it cannot reduce
        # today's candidate volume, only add genuinely-coordinated multi-wallet
        # candidates on top.
        self._recent_events: dict[tuple, list[ScoredEvent]] = defaultdict(list)
        # Wallet-set signatures already emitted per (pool, event_type), with the
        # timestamp of the event that triggered emission — prevents re-emitting the
        # exact same wallet group as a "new" candidate on every subsequent event
        # within the window, while still allowing a genuinely larger cluster (a new
        # wallet joining) to be emitted as its own candidate.
        self._emitted_signatures: dict[tuple, dict[frozenset, datetime]] = defaultdict(dict)

        # Solo-signal dedup — found 2026-07-19: the solo emission path had no
        # cooldown at all, so the same wallet repeating the same action on the
        # same pool (e.g. a baseline recalculating between two mints seconds
        # apart) produced near-duplicate alerts with different z-scores/
        # confidence for what a subscriber reads as the same event. Distinct
        # from the multi-wallet dedup above — this tracks one wallet's own
        # repeat activity, not overlapping wallet groups.
        self._solo_last_emitted: dict[tuple, datetime] = {}

    def _should_emit_solo(self, scored: ScoredEvent) -> bool:
        """False if this (wallet, pool, event_type) already fired a solo
        candidate within SOLO_SIGNAL_COOLDOWN_MINUTES."""
        key  = (scored.wallet_address, scored.pool_address, scored.event_type)
        last = self._solo_last_emitted.get(key)
        if last is not None and scored.timestamp - last < timedelta(minutes=SOLO_COOLDOWN_MINUTES):
            log.debug(
                "Solo signal suppressed (cooldown): wallet=%s pool=%s type=%s",
                scored.wallet_address, scored.pool_address, scored.event_type,
            )
            return False
        self._solo_last_emitted[key] = scored.timestamp
        return True

    def _prune_solo_emitted(self, now: datetime) -> None:
        """Unlike the pool-keyed dicts above (bounded by pool_registry size),
        this is keyed by wallet address too, so the key space grows with
        every distinct wallet ever seen — must be pruned or it leaks memory
        over a long-running process."""
        cutoff = now - timedelta(minutes=SOLO_COOLDOWN_MINUTES)
        stale  = [k for k, ts in self._solo_last_emitted.items() if ts < cutoff]
        for k in stale:
            del self._solo_last_emitted[k]

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

        # Every event that clears the z-score threshold is emitted as (at
        # minimum) a solo-wallet candidate — unless this same wallet already
        # fired one for this pool/event_type within SOLO_COOLDOWN_MINUTES
        # (see _should_emit_solo's docstring for why this dedup exists).
        if self._should_emit_solo(scored):
            await self._emit_candidate(self._clusterer._solo_cluster(scored), [scored], r)

        # Additionally check if this event, combined with other recently
        # flagged events on the same pool, now forms a real multi-wallet
        # cluster — see the constructor comment for why this is additive.
        await self._check_multi_wallet_cluster(scored, r)

        # Every 100 events: prune the solo-dedup dict and log stats
        if self._processed % 100 == 0:
            self._prune_solo_emitted(scored.timestamp)
            log.info("Stats: %s", self._scorer.stats)

    async def _check_multi_wallet_cluster(self, scored: ScoredEvent, r) -> None:
        """Buffer recent same-(pool,event_type) events and emit any newly-formed
        multi-wallet cluster the just-arrived event completes."""
        key    = (scored.pool_address, scored.event_type)
        window = timedelta(minutes=CLUSTER_WINDOW_MINUTES)
        cutoff = scored.timestamp - window

        buf = self._recent_events[key]
        buf.append(scored)
        buf[:] = [e for e in buf if e.timestamp >= cutoff]

        sigs = self._emitted_signatures[key]
        for sig, ts in list(sigs.items()):
            if ts < cutoff:
                del sigs[sig]

        for cluster in self._clusterer.cluster(buf):
            if cluster.wallet_count < 2:
                continue  # solo case already handled unconditionally in _process
            if scored.wallet_address not in cluster.wallets:
                continue

            signature = frozenset(cluster.wallets)
            if signature in sigs:
                continue  # this exact wallet group was already emitted

            sigs[signature] = scored.timestamp
            raw_events = [e for e in buf if e.wallet_address in cluster.wallets]
            await self._emit_candidate(cluster, raw_events, r)
            log.info(
                "🐋🐋 Multi-wallet cluster: %d wallets, z=%.2f, usd=%.0f",
                cluster.wallet_count, cluster.z_score, cluster.total_volume_usd,
            )

    async def _emit_candidate(self, cluster: WalletCluster, raw_events: list[ScoredEvent], r) -> None:
        candidate = AnomalyCandidate(cluster=cluster, raw_events=raw_events)

        payload = json.dumps(candidate.to_dict())
        await r.lpush("mantis:anomaly_candidates", payload)
        await r.ltrim("mantis:anomaly_candidates", 0, 999)

        self._candidates += 1
        await r.set("mantis:stats:candidates", self._candidates)
        log.info(
            "🎯 Candidate queued: z=%.2f protocol=%s type=%s usd=%.0f wallets=%d",
            cluster.z_score, cluster.protocol, cluster.event_type,
            cluster.total_volume_usd, cluster.wallet_count,
        )


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
