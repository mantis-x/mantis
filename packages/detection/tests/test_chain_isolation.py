"""Tests for multi-chain baseline isolation in BaselineStore."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import time
from src.baselines.pool_baseline import BaselineStore, BUCKET_SECONDS


def _seed(store, pool, chain, mean_usd, hours=48):
    import random
    random.seed(42)
    now = time.time()
    for hour in range(hours):
        ts = now - hours * BUCKET_SECONDS + hour * BUCKET_SECONDS
        store.record(pool, "swap", max(0, random.gauss(mean_usd, mean_usd * 0.1)), ts, chain=chain)


def test_same_pool_different_chains_have_separate_baselines():
    store = BaselineStore()
    pool = "0xsamepool"

    _seed(store, pool, "mantle",   mean_usd=100_000)
    _seed(store, pool, "arbitrum", mean_usd=5_000_000)

    z_mantle = store.z_score(pool, "swap", 10_000_000.0, chain="mantle")
    z_arb    = store.z_score(pool, "swap", 10_000_000.0, chain="arbitrum")

    assert z_mantle is not None, "Mantle baseline should have enough data"
    assert z_arb    is not None, "Arbitrum baseline should have enough data"
    assert z_mantle > z_arb, (
        f"$10M should flag Mantle (mean~$100K) far harder than Arbitrum (mean~$5M): "
        f"z_mantle={z_mantle:.1f}, z_arb={z_arb:.1f}"
    )


def test_pool_count_per_chain():
    store = BaselineStore()
    now = time.time()
    for hour in range(48):
        ts = now - 48 * BUCKET_SECONDS + hour * BUCKET_SECONDS
        store.record("0xpool", "swap", 1000.0, ts, chain="mantle")
        store.record("0xpool", "swap", 9_000_000.0, ts, chain="arbitrum")

    # (mantle, 0xpool, swap) and (arbitrum, 0xpool, swap) are distinct keys
    assert store.pool_count() == 2


def test_mantle_baseline_unaffected_by_arbitrum_volume():
    store = BaselineStore()
    pool = "0xpool_mantle_arb"
    _seed(store, pool, "mantle",   mean_usd=50_000)
    _seed(store, pool, "arbitrum", mean_usd=5_000_000)

    # A 10× Mantle-mean event should still register as highly anomalous
    z = store.z_score(pool, "swap", 500_000.0, chain="mantle")
    assert z is not None
    assert z > 5.0, f"10× the Mantle mean should have a very high z-score, got {z}"


def test_z_score_missing_chain_returns_none():
    store = BaselineStore()
    now = time.time()
    for hour in range(48):
        ts = now - 48 * BUCKET_SECONDS + hour * BUCKET_SECONDS
        store.record("0xpool", "swap", 1000.0, ts, chain="mantle")

    # Arbitrum was never seeded
    z = store.z_score("0xpool", "swap", 1000.0, chain="arbitrum")
    assert z is None


if __name__ == "__main__":
    test_same_pool_different_chains_have_separate_baselines()
    test_pool_count_per_chain()
    test_mantle_baseline_unaffected_by_arbitrum_volume()
    test_z_score_missing_chain_returns_none()
    print("All chain isolation tests passed")
