"""
Functional test for the 2026-07-21 multi-wallet clustering rewrite (fix a).

Reproduces the exact scenario the old design could never catch: several
*individually sub-threshold* events from distinct wallets on the same pool
whose *combined* window volume is anomalous. The old path filtered each event
through z-score first, so these never reached clustering; the new path buffers
all scorable events and z-scores the aggregate.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from src.detector import Detector


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


def _seed(detector):
    # 48 hourly buckets, mean ~100, non-zero variance so std > 0.
    history = []
    for i in range(48):
        ts = (NOW - timedelta(hours=48 - i)).timestamp()
        vol = 100 + ((i % 5) - 2) * 20   # 60..140, mean 100
        history.append({
            "chain": CHAIN, "pool_address": POOL,
            "event_type": "swap", "amount_usd": vol, "timestamp": ts,
        })
    detector._store.seed_from_historical(history)


def _event(wallet, usd, offset_min):
    ts = (NOW + timedelta(minutes=offset_min)).isoformat()
    return {
        "chain": CHAIN, "block": 1, "tx_full": f"0x{wallet[-4:]}{offset_min}",
        "protocol": "uniswap_v3", "pool_full": POOL,
        "wallet_full": wallet, "type": "swap", "usd": usd, "ts": ts,
    }


def _candidates(fake):
    return [json.loads(p) for p in fake.lists["mantis:anomaly_candidates"]]


def test_subthreshold_wallets_form_aggregate_cluster():
    detector = Detector(redis_url="")
    _seed(detector)
    fake = FakeRedis()

    # Each event is ~$80 — individually BELOW threshold (z = (80-100)/std < 2.5).
    # Combined across 3 distinct wallets in a 5-min window they far exceed a
    # typical hour's volume, so the aggregate is anomalous.
    async def run():
        await detector._process(_event("0xwalletaaaa", 80, 0), fake)
        await detector._process(_event("0xwalletbbbb", 80, 2), fake)
        await detector._process(_event("0xwalletcccc", 80, 4), fake)

    asyncio.run(run())

    cands = _candidates(fake)
    multi = [c for c in cands if len(c["wallets"]) >= 2]
    solo  = [c for c in cands if len(c["wallets"]) == 1]

    assert multi, f"expected a multi-wallet candidate, got {cands}"
    assert not solo, f"no single event was anomalous, so no solo candidate expected: {solo}"
    top = max(multi, key=lambda c: len(c["wallets"]))
    assert len(top["wallets"]) == 3
    assert top["total_volume_usd"] >= 200
    assert top["z_score"] >= 2.5


def test_same_wallet_repeats_do_not_cluster():
    """One wallet acting repeatedly must NOT form a multi-wallet cluster."""
    detector = Detector(redis_url="")
    _seed(detector)
    fake = FakeRedis()

    async def run():
        await detector._process(_event("0xwalletaaaa", 80, 0), fake)
        await detector._process(_event("0xwalletaaaa", 80, 2), fake)
        await detector._process(_event("0xwalletaaaa", 80, 4), fake)

    asyncio.run(run())

    multi = [c for c in _candidates(fake) if len(c["wallets"]) >= 2]
    assert not multi, f"single wallet should never form a multi-wallet cluster: {multi}"


def test_wallet_set_dedup_then_reemit_on_new_wallet():
    """Same wallet set does not re-emit; a genuinely new wallet joining does."""
    detector = Detector(redis_url="")
    _seed(detector)
    fake = FakeRedis()

    async def run():
        await detector._process(_event("0xwalletaaaa", 120, 0), fake)
        await detector._process(_event("0xwalletbbbb", 120, 1), fake)   # {a,b} fires
        await detector._process(_event("0xwalletaaaa", 120, 2), fake)   # {a,b} again → suppressed
        await detector._process(_event("0xwalletcccc", 120, 3), fake)   # {a,b,c} → new, re-emit

    asyncio.run(run())

    multi = [c for c in _candidates(fake) if len(c["wallets"]) >= 2]
    sizes = sorted(len(c["wallets"]) for c in multi)
    assert sizes == [2, 3], f"expected one 2-wallet then one 3-wallet emit, got {sizes}"
