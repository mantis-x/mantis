"""
2026-07-24: MIN_CANDIDATE_USD pre-enrichment dollar floor.

Mantle/HashKey pool baselines are calibrated to genuinely tiny real volume
(means as low as $3-35/hour), so ordinary noise-level activity clears the
z-score bar and used to reach enrichment (and Claude) every time despite a
100% observed discard rate. This floor skips solo candidates below the
threshold before they're ever emitted, at zero cost to the multi-wallet path
(already gated far above this by MULTIWALLET_MIN_USD).
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from src.detector import Detector

POOL  = "0xpool00000000000000000000000000000000abcd"
CHAIN = "testchain"
NOW   = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


class FakeRedis:
    def __init__(self):
        self.lists = defaultdict(list)

    async def lpush(self, k, v):
        self.lists[k].insert(0, v)

    async def ltrim(self, k, a, b):
        self.lists[k] = self.lists[k][a : (None if b == -1 else b + 1)]

    async def set(self, k, v):
        pass


def _seed(detector, mean, pool=POOL, etype="swap", chain=CHAIN):
    history = []
    for i in range(48):
        ts = (NOW - timedelta(hours=48 - i)).timestamp()
        vol = max(mean * 0.1, mean + ((i % 5) - 2) * (mean * 0.2))
        history.append({
            "chain": chain, "pool_address": pool,
            "event_type": etype, "amount_usd": vol, "timestamp": ts,
        })
    detector._store.seed_from_historical(history)


def _event(wallet, usd, offset_min, pool=POOL, etype="swap", chain=CHAIN):
    ts = (NOW + timedelta(minutes=offset_min)).isoformat()
    return {
        "chain": chain, "block": 1, "tx_full": f"0x{wallet[-4:]}{offset_min}",
        "protocol": "agni_finance", "pool_full": pool,
        "wallet_full": wallet, "type": etype, "usd": usd, "ts": ts,
    }


def _solo_candidates(fake):
    cands = [json.loads(p) for p in fake.lists["mantis:anomaly_candidates"]]
    return [c for c in cands if len(c["wallets"]) == 1]


def test_subfloor_solo_candidate_is_suppressed():
    """A Mantle-scale anomaly ($20 on a $3-mean pool, comfortably z>2.5) below
    MIN_CANDIDATE_USD ($300 default) must never reach the candidate queue."""
    detector = Detector(redis_url="")
    _seed(detector, mean=3)
    fake = FakeRedis()

    asyncio.run(detector._process(_event("0xwalletaaaa", 20, 0), fake))

    assert not _solo_candidates(fake), "sub-floor candidate should have been skipped before emission"


def test_above_floor_solo_candidate_still_emits():
    """A genuinely anomalous event above the floor must be unaffected."""
    detector = Detector(redis_url="")
    _seed(detector, mean=3)
    fake = FakeRedis()

    asyncio.run(detector._process(_event("0xwalletbbbb", 500, 0), fake))

    solo = _solo_candidates(fake)
    assert len(solo) == 1, f"above-floor candidate should still emit: {solo}"
    assert solo[0]["total_volume_usd"] == 500
