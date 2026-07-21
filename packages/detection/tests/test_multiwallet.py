"""
Functional test for the 2026-07-21 multi-wallet clustering rewrite (fix a).

Reproduces the exact scenario the old design could never catch: several
*individually sub-threshold* events from distinct wallets on the same pool
whose *combined* window volume is anomalous. The old path z-score-gated each
event BEFORE clustering, so these never reached it; the new path buffers all
scorable events and z-scores the aggregate.

Also covers the guards added after the first canary (which fired on tiny HashKey
transfer bursts): `transfer` event types are excluded, and clusters below
MULTIWALLET_MIN_USD are suppressed.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from src.detector import Detector, _MULTIWALLET_MIN_USD


class FakeRedis:
    def __init__(self):
        self.lists = defaultdict(list)
        self.kv = {}

    async def lpush(self, k, v):
        self.lists[k].insert(0, v)

    async def ltrim(self, k, a, b):
        self.lists[k] = self.lists[k][a : (None if b == -1 else b + 1)]

    async def set(self, k, v):
        self.kv[k] = v


POOL  = "0xpool00000000000000000000000000000000abcd"
CHAIN = "testchain"
NOW   = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _seed(detector, mean, pool=POOL, etype="swap"):
    # 48 hourly buckets around `mean` with non-zero variance so std > 0.
    history = []
    for i in range(48):
        ts = (NOW - timedelta(hours=48 - i)).timestamp()
        vol = mean + ((i % 5) - 2) * (mean * 0.2)   # +-40% swing, mean preserved
        history.append({
            "chain": CHAIN, "pool_address": pool,
            "event_type": etype, "amount_usd": vol, "timestamp": ts,
        })
    detector._store.seed_from_historical(history)


def _event(wallet, usd, offset_min, pool=POOL, etype="swap"):
    ts = (NOW + timedelta(minutes=offset_min)).isoformat()
    return {
        "chain": CHAIN, "block": 1, "tx_full": f"0x{wallet[-4:]}{offset_min}",
        "protocol": "uniswap_v3", "pool_full": pool,
        "wallet_full": wallet, "type": etype, "usd": usd, "ts": ts,
    }


def _candidates(fake):
    return [json.loads(p) for p in fake.lists["mantis:anomaly_candidates"]]


def test_subthreshold_wallets_form_aggregate_cluster():
    detector = Detector(redis_url="")
    _seed(detector, mean=20_000)        # whale-scale pool, std ~ 5.6k
    fake = FakeRedis()

    # Each ~$20k is individually AT/BELOW the mean (z<2.5), but 6 distinct wallets
    # combining to ~$120k is anomalous AND clears the $100k MULTIWALLET_MIN_USD floor.
    async def run():
        for n, w in enumerate(["aaaa", "bbbb", "cccc", "dddd", "eeee", "ffff"]):
            await detector._process(_event(f"0xwallet{w}", 20_000, n), fake)

    asyncio.run(run())

    cands = _candidates(fake)
    multi = [c for c in cands if len(c["wallets"]) >= 2]
    solo  = [c for c in cands if len(c["wallets"]) == 1]

    assert multi, f"expected a multi-wallet candidate, got {cands}"
    assert not solo, f"no single event was anomalous, so no solo candidate expected: {solo}"
    top = max(multi, key=lambda c: len(c["wallets"]))
    assert len(top["wallets"]) == 6
    assert top["total_volume_usd"] >= _MULTIWALLET_MIN_USD
    assert top["z_score"] >= 2.5


def test_same_wallet_repeats_do_not_cluster():
    detector = Detector(redis_url="")
    _seed(detector, mean=20_000)
    fake = FakeRedis()

    async def run():
        await detector._process(_event("0xwalletaaaa", 60_000, 0), fake)
        await detector._process(_event("0xwalletaaaa", 60_000, 2), fake)
        await detector._process(_event("0xwalletaaaa", 60_000, 4), fake)

    asyncio.run(run())

    multi = [c for c in _candidates(fake) if len(c["wallets"]) >= 2]
    assert not multi, f"single wallet should never form a multi-wallet cluster: {multi}"


def test_wallet_set_dedup_then_reemit_on_new_wallet():
    detector = Detector(redis_url="")
    _seed(detector, mean=60_000)        # each $60k event is sub-threshold; pairs clear $100k
    fake = FakeRedis()

    async def run():
        await detector._process(_event("0xwalletaaaa", 60_000, 0), fake)
        await detector._process(_event("0xwalletbbbb", 60_000, 1), fake)   # {a,b}=$120k fires
        await detector._process(_event("0xwalletaaaa", 60_000, 2), fake)   # {a,b} again → suppressed
        await detector._process(_event("0xwalletcccc", 60_000, 3), fake)   # {a,b,c} → new, re-emit

    asyncio.run(run())

    multi = [c for c in _candidates(fake) if len(c["wallets"]) >= 2]
    sizes = sorted(len(c["wallets"]) for c in multi)
    assert sizes == [2, 3], f"expected one 2-wallet then one 3-wallet emit, got {sizes}"


def test_transfer_event_type_excluded():
    """HashKey-style flow transfers must not drive multi-wallet clustering even
    when their aggregate is anomalous and above the USD floor — this was the
    live over-firing bug."""
    detector = Detector(redis_url="")
    _seed(detector, mean=20_000, etype="transfer")
    fake = FakeRedis()

    async def run():
        for n, w in enumerate(["aaaa", "bbbb", "cccc", "dddd", "eeee", "ffff"]):
            await detector._process(_event(f"0xwallet{w}", 20_000, n, etype="transfer"), fake)

    asyncio.run(run())

    multi = [c for c in _candidates(fake) if len(c["wallets"]) >= 2]
    assert not multi, f"transfer flow must be excluded from multi-wallet path: {multi}"


def test_below_min_usd_suppressed():
    """A genuinely z-anomalous multi-wallet cluster that is still below the USD
    floor must be suppressed — this is what would have delivered the live
    low-value HashKey/Mantle noise."""
    small_pool = "0xsmallpool000000000000000000000000000abcd"
    detector = Detector(redis_url="")
    _seed(detector, mean=20_000, pool=small_pool)     # std ~5.6k
    fake = FakeRedis()

    async def run():
        # 3 distinct wallets, aggregate ~$60k — strongly anomalous (z>7) vs the
        # $20k baseline mean, but below the $100k floor, so no candidate.
        await detector._process(_event("0xwalletaaaa", 20_000, 0, pool=small_pool), fake)
        await detector._process(_event("0xwalletbbbb", 20_000, 2, pool=small_pool), fake)
        await detector._process(_event("0xwalletcccc", 20_000, 4, pool=small_pool), fake)

    asyncio.run(run())

    multi = [c for c in _candidates(fake) if len(c["wallets"]) >= 2]
    assert not multi, f"sub-${_MULTIWALLET_MIN_USD:.0f} cluster should be suppressed: {multi}"
