"""
WalletTrackRecordLabeler — the free, no-API-key alternative to Nansen.
Exercised against fakeredis (real Redis command semantics, no network).
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fakeredis

from src.wallet_track_record import (
    WalletTrackRecordLabeler, WALLET_HITS_KEY, WALLET_TOTAL_KEY,
)


def _labeler_with(hits: dict, totals: dict) -> WalletTrackRecordLabeler:
    labeler = WalletTrackRecordLabeler(redis_url="redis://localhost:6379/0")
    labeler._redis = fakeredis.FakeStrictRedis(decode_responses=True)
    for wallet, n in hits.items():
        labeler._redis.hset(WALLET_HITS_KEY, wallet, n)
    for wallet, n in totals.items():
        labeler._redis.hset(WALLET_TOTAL_KEY, wallet, n)
    return labeler


class TestIsSmartMoney:
    def test_below_min_samples_returns_none(self):
        labeler = _labeler_with(hits={"0xaaa": 2}, totals={"0xaaa": 2})
        assert labeler.is_smart_money("0xAAA") is None  # 2 < MIN_SAMPLES default 3

    def test_high_hit_rate_above_min_samples_returns_true(self):
        labeler = _labeler_with(hits={"0xaaa": 4}, totals={"0xaaa": 5})
        assert labeler.is_smart_money("0xAAA") is True  # 80% >= 65% threshold

    def test_low_hit_rate_above_min_samples_returns_false(self):
        labeler = _labeler_with(hits={"0xaaa": 1}, totals={"0xaaa": 5})
        assert labeler.is_smart_money("0xAAA") is False  # 20% < 65% threshold

    def test_unknown_wallet_returns_none(self):
        labeler = _labeler_with(hits={}, totals={})
        assert labeler.is_smart_money("0xNeverSeen") is None

    def test_address_lookup_is_case_insensitive(self):
        labeler = _labeler_with(hits={"0xaaa": 4}, totals={"0xaaa": 5})
        assert labeler.is_smart_money("0xAAA") is True
        assert labeler.is_smart_money("0xaaa") is True

    def test_redis_failure_returns_none_not_crash(self):
        labeler = WalletTrackRecordLabeler(redis_url="redis://localhost:6379/0")

        class _BrokenRedis:
            def pipeline(self):
                raise ConnectionError("redis down")

        labeler._redis = _BrokenRedis()
        assert labeler.is_smart_money("0xaaa") is None
