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
from src.algorithms.clustering import WalletClusterer, CLUSTER_WINDOW_MINUTES, MIN_CLUSTER_SIZE
from src.baselines.pool_baseline import BaselineStore
from src.models.candidate import AnomalyCandidate, WalletCluster, ScoredEvent

# Cap on buffered events per (pool, event_type) window — bounds memory on busy
# pools (an active Uniswap pool can log hundreds of events in 30 min). The
# window prune already bounds this by time; this is a hard safety ceiling.
_MAX_BUFFER_PER_KEY = int(os.getenv("CLUSTER_BUFFER_MAX", "500"))

# Multi-wallet coordinated accumulation is a DEX phenomenon (swap/mint/burn).
# HashKey's token-transfer flow monitoring produces many tiny same-window
# transfers from distinct wallets that clear the aggregate z-score against its
# very small baseline but are NOT coordinated whale activity (observed live
# 2026-07-21: dozens of $28-$93 "clusters"). Exclude those event types from the
# multi-wallet path — the solo flow-monitoring path still covers HashKey.
_MULTIWALLET_EXCLUDE_TYPES = set(
    t.strip() for t in os.getenv("MULTIWALLET_EXCLUDE_TYPES", "transfer").split(",") if t.strip()
)
# Floor on a multi-wallet cluster's aggregate USD. Set to $100k (2026-07-21,
# user decision) to target genuine whale-scale coordination only — the real
# whale signals this project has produced were all $200k-$3.2M, while the live
# over-firing noise was sub-$100. This deliberately excludes retail-scale
# coordination on low-baseline chains (Mantle/HashKey); tune via env without a
# redeploy if that ever needs revisiting.
_MULTIWALLET_MIN_USD = float(os.getenv("MULTIWALLET_MIN_USD", "100000"))

# Per-chain override cache, same pattern as zscore.py's ZSCORE_THRESHOLD_<CHAIN>.
# Added 2026-07-23: Ethereum's real swap volume (only decodable since #30's
# topic fix) still produces $260-270K multi-wallet clusters at 62-68%
# confidence — legitimate per the $100k floor, but watched live to see if
# they're too frequent to be useful. Set MULTIWALLET_MIN_USD_ETHEREUM (e.g.
# to 1000000) to raise Ethereum's floor specifically without touching
# Arbitrum/Mantle/HashKey or redeploying.
_multiwallet_min_usd_cache: dict[str, float] = {}


def _multiwallet_min_usd_for_chain(chain: str) -> float:
    if chain not in _multiwallet_min_usd_cache:
        env_key = f"MULTIWALLET_MIN_USD_{chain.upper()}"
        _multiwallet_min_usd_cache[chain] = float(os.getenv(env_key, str(_MULTIWALLET_MIN_USD)))
    return _multiwallet_min_usd_cache[chain]

# Ceiling on a multi-wallet cluster's wallet count. Found 2026-07-23: once
# the UNIV3_SWAP_TOPIC fix (see PROJECT_STATE.md #30) let real Uniswap V3
# swaps flow on Arbitrum/Ethereum for the first time, a liquid pool like
# Ethereum's USDC/WETH naturally has dozens of DISTINCT unrelated wallets
# trading within any 30-min window — that's ordinary market liquidity, not
# coordination. Real coordinated accumulation (the signals this project has
# actually produced) involved a small, plausible number of related actors —
# observed as high as 5-6 wallets, never dozens. Beyond this ceiling, treat
# the window as organic volume and don't emit, however large the aggregate.
_MULTIWALLET_MAX_WALLETS = int(os.getenv("MULTIWALLET_MAX_WALLETS", "15"))

# Cooldown before the SAME (pool, event_type) can emit another multi-wallet
# candidate at all, regardless of new wallets joining. Found 2026-07-23: the
# wallet-set-signature dedup only suppresses re-emitting the exact same
# group — a busy pool where a new distinct wallet trades every few seconds
# produced a fresh "larger" signature (and a fresh alert) roughly every
# 20-60 seconds, all describing the same ongoing organic trading, not dozens
# of distinct events. This cooldown caps it to at most one alert per window
# per pool — a genuinely new coordination event still gets through once the
# cooldown lapses.
MULTIWALLET_COOLDOWN_MINUTES = int(os.getenv("MULTIWALLET_COOLDOWN_MINUTES", str(CLUSTER_WINDOW_MINUTES)))

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

        # Real multi-wallet clustering — 2026-07-21 rewrite. The first attempt
        # (2026-07-19) buffered only events that had *already* cleared the z-score
        # gate, then required >=2 such whales on the same pool/type within the
        # window. Production proved that precondition statistically unreachable:
        # ~19 threshold-crossing events in a week across 11 (pool,type) buckets,
        # the single closest pair 69 min apart (2.3x the 30-min window) — zero
        # multi-wallet clusters ever emitted. The deeper flaw: real coordinated
        # accumulation is many wallets each doing *individually-modest* trades that
        # are only anomalous in aggregate, but filtering each event through z-score
        # first drops exactly those before clustering can see them.
        #
        # New approach: buffer ALL scorable events per (pool, event_type) —
        # not just anomalies (see ZScoreDetector.evaluate) — and on each new event
        # compute an *aggregate* z-score over the combined volume of the >=2
        # distinct wallets active in the window, against the same hourly pool
        # baseline the solo path uses. A window whose combined multi-wallet volume
        # is itself anomalous is coordination no single event would have surfaced.
        # This is still additive to the solo path, which is unchanged.
        self._recent_events: dict[tuple, list[ScoredEvent]] = defaultdict(list)
        # Wallet-set signatures already emitted per (pool, event_type), with the
        # timestamp of the event that triggered emission — prevents re-emitting the
        # exact same wallet group as a "new" candidate on every subsequent event
        # within the window, while still allowing a genuinely larger cluster (a new
        # wallet joining) to be emitted as its own candidate.
        self._emitted_signatures: dict[tuple, dict[frozenset, datetime]] = defaultdict(dict)
        # Last time a multi-wallet candidate was emitted per (pool, event_type)
        # — see MULTIWALLET_COOLDOWN_MINUTES above for why this exists.
        self._multiwallet_last_emitted: dict[tuple, datetime] = {}

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
        # evaluate() records the baseline and returns the event with its z-score
        # even when it's below threshold — the multi-wallet path needs those.
        scored = self._scorer.evaluate(event)

        if scored is None:
            return   # baseline not ready yet

        # Every event that clears the z-score threshold is emitted as (at
        # minimum) a solo-wallet candidate — unless this same wallet already
        # fired one for this pool/event_type within SOLO_COOLDOWN_MINUTES
        # (see _should_emit_solo's docstring for why this dedup exists).
        if scored.is_anomaly and self._should_emit_solo(scored):
            await self._emit_candidate(self._clusterer._solo_cluster(scored), [scored], r)

        # Additionally check if this event, combined with other recent events on
        # the same pool (anomalous or not), now forms a coordinated multi-wallet
        # cluster whose *aggregate* volume is anomalous — see the constructor
        # comment for why this is additive and why it buffers sub-threshold events.
        await self._check_multi_wallet_cluster(scored, r)

        # Every 100 events: prune the solo-dedup dict and log stats
        if self._processed % 100 == 0:
            self._prune_solo_emitted(scored.timestamp)
            log.info("Stats: %s", self._scorer.stats)

    async def _check_multi_wallet_cluster(self, scored: ScoredEvent, r) -> None:
        """Buffer recent same-(pool,event_type) events (anomalous or not) and emit
        a multi-wallet candidate when >=2 distinct wallets' *combined* window
        volume is itself anomalous against the pool baseline."""
        # Coordinated-accumulation clustering is a DEX concept — skip flow-
        # monitoring transfer events (HashKey), which otherwise fire on tiny
        # transfer bursts against a small baseline. Solo path still covers them.
        if scored.event_type in _MULTIWALLET_EXCLUDE_TYPES:
            return

        key    = (scored.pool_address, scored.event_type)
        window = timedelta(minutes=CLUSTER_WINDOW_MINUTES)
        cutoff = scored.timestamp - window

        buf = self._recent_events[key]
        buf.append(scored)
        buf[:] = [e for e in buf if e.timestamp >= cutoff]
        if len(buf) > _MAX_BUFFER_PER_KEY:
            del buf[:-_MAX_BUFFER_PER_KEY]

        sigs = self._emitted_signatures[key]
        for sig, ts in list(sigs.items()):
            if ts < cutoff:
                del sigs[sig]

        wallets = sorted({e.wallet_address for e in buf})
        if len(wallets) < MIN_CLUSTER_SIZE:
            return   # not multi-wallet yet — solo path already handled anomalies

        # Ceiling: beyond this many distinct wallets in the window, this is
        # ordinary market liquidity on a busy pool, not coordination — see the
        # constant's docstring (found 2026-07-23 on Ethereum's blue-chip pools).
        if len(wallets) > _MULTIWALLET_MAX_WALLETS:
            return

        # Aggregate the combined volume of the coordinated window and z-score it
        # against the same (pool, event_type) hourly baseline the solo path uses.
        # A 30-min window is <= one baseline bucket, so this is a conservative
        # comparison (partial-hour sum vs full-hour distribution) — it won't fire
        # on normal activity, only when a short-window multi-wallet burst already
        # exceeds a typical *hour* of volume by the threshold.
        aggregate_usd = sum(e.amount_usd for e in buf)
        if aggregate_usd < _multiwallet_min_usd_for_chain(scored.chain):
            return   # not economically meaningful — don't spend an enrichment call

        z_agg = self._store.z_score(
            scored.pool_address, scored.event_type, aggregate_usd, chain=scored.chain,
        )
        if z_agg is None or z_agg < self._scorer.threshold_for(scored.chain):
            return

        # Cooldown: at most one multi-wallet emission per (pool, event_type)
        # per MULTIWALLET_COOLDOWN_MINUTES, regardless of new wallets joining.
        # Found 2026-07-23: without this, a busy pool where a new distinct
        # wallet trades every few seconds produced a fresh "larger" signature
        # (see below) — and a fresh alert — roughly every 20-60 seconds, all
        # describing the same ongoing organic trading, not distinct events.
        last_emitted = self._multiwallet_last_emitted.get(key)
        if last_emitted is not None and scored.timestamp - last_emitted < timedelta(minutes=MULTIWALLET_COOLDOWN_MINUTES):
            return

        # Dedup by exact wallet set — a genuinely new wallet joining yields a
        # larger set (new signature) and re-emits; the same group repeating does
        # not. Suppressed groups still stay in the buffer, so a later join still
        # forms the larger cluster. (The cooldown above already blocks nearly
        # all of what this used to catch alone; kept as a secondary guard.)
        signature = frozenset(wallets)
        if signature in sigs:
            return
        sigs[signature] = scored.timestamp
        self._multiwallet_last_emitted[key] = scored.timestamp

        cluster = WalletCluster(
            wallets          = list(wallets),
            chain            = scored.chain,
            pool_address     = scored.pool_address,
            protocol         = scored.protocol,
            event_type       = scored.event_type,
            total_volume_usd = aggregate_usd,
            z_score          = round(z_agg, 3),
            event_count      = len(buf),
            first_seen       = min(e.timestamp for e in buf),
            last_seen        = max(e.timestamp for e in buf),
        )
        await self._emit_candidate(cluster, list(buf), r)
        log.info(
            "🐋🐋 Multi-wallet cluster: %d wallets, aggregate z=%.2f, usd=%.0f",
            len(wallets), z_agg, aggregate_usd,
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
