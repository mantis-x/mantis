"""Tests for the z-score detector."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import time
from datetime import datetime, timezone
from src.baselines.pool_baseline import BaselineStore, BUCKET_SECONDS
from src.algorithms.zscore import ZScoreDetector
from src.models.candidate import ScoredEvent


def make_event(amount_usd=100.0, pool="0xpool1", etype="swap", ts=None):
    class E:
        block_number   = 1
        tx_hash        = "0xabc"
        protocol       = type("P", (), {"value": "agni_finance"})()
        pool_address   = pool
        wallet_address = "0xwallet1"
        event_type     = type("E", (), {"value": etype})()
        amount_usd_    = amount_usd
        timestamp      = ts or datetime.now(tz=timezone.utc)
    e = E()
    e.amount_usd = amount_usd
    return e


def seed_baseline(store, pool, etype, mean=50_000, std=10_000, hours=30):
    """Seed a baseline with realistic hourly volumes."""
    import random
    random.seed(0)
    now = time.time()
    for i in range(hours):
        ts = now - (hours - i) * BUCKET_SECONDS
        vol = max(0, random.gauss(mean, std))
        store.record(pool, etype, vol, ts)


def test_no_baseline_returns_none():
    store    = BaselineStore()
    detector = ZScoreDetector(store)
    event    = make_event(amount_usd=1_000_000)
    result   = detector.score_event(event)
    assert result is None, "Should return None before baseline is built"


def test_normal_event_not_flagged():
    store    = BaselineStore()
    seed_baseline(store, "0xpool1", "swap", mean=50_000, std=10_000)
    detector = ZScoreDetector(store)

    # Normal event — within 1 std of mean
    event  = make_event(amount_usd=55_000, pool="0xpool1", etype="swap")
    result = detector.score_event(event)
    assert result is None, "Normal volume should not be flagged"


def test_anomalous_event_flagged():
    store    = BaselineStore()
    seed_baseline(store, "0xpool1", "swap", mean=50_000, std=10_000)
    detector = ZScoreDetector(store)

    # 5 standard deviations above mean = very anomalous
    event  = make_event(amount_usd=150_000, pool="0xpool1", etype="swap")
    result = detector.score_event(event)
    assert result is not None, "High-volume event should be flagged"
    assert result.z_score > 2.5
    assert result.amount_usd == 150_000


def test_z_score_value_correct():
    store = BaselineStore()
    # Manually set up a known baseline
    now = time.time()
    for i in range(30):
        ts = now - (30 - i) * BUCKET_SECONDS
        store.record("0xpool2", "mint", 100.0, ts)  # constant 100 USD/hour

    # std will be 0 for constant data → should return None
    detector = ZScoreDetector(store)
    event    = make_event(amount_usd=200.0, pool="0xpool2", etype="mint")
    result   = detector.score_event(event)
    assert result is None, "Zero-std baseline should return None"


def test_batch_scoring():
    store    = BaselineStore()
    seed_baseline(store, "0xpool3", "swap", mean=20_000, std=5_000)
    detector = ZScoreDetector(store)

    events = [
        make_event(amount_usd=21_000, pool="0xpool3"),   # normal
        make_event(amount_usd=22_000, pool="0xpool3"),   # normal
        make_event(amount_usd=100_000, pool="0xpool3"),  # anomalous
    ]
    results = detector.score_batch(events)
    assert len(results) == 1, "Only the anomalous event should be returned"
    assert results[0].amount_usd == 100_000


if __name__ == "__main__":
    test_no_baseline_returns_none()
    test_normal_event_not_flagged()
    test_anomalous_event_flagged()
    test_z_score_value_correct()
    test_batch_scoring()
    print("✓ All z-score tests passed")
