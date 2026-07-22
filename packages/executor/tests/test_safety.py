"""
Tests for packages/executor/src/safety.py — the kill switch, signal-dedup,
and daily-spend-tracking primitives from docs/execute_readiness.md's §3/§4
checklist. Exercised against fakeredis (real Redis command semantics, no
network), same discipline as test_queue_fanout.py.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import fakeredis.aioredis as fakeredis_aio

from src.safety import (
    signal_fingerprint,
    is_kill_switch_active,
    is_duplicate_signal,
    get_daily_spent_usd,
    record_execution_spend,
    KILL_SWITCH_KEY,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def redis_client():
    return fakeredis_aio.FakeRedis(decode_responses=True)


def _signal(**overrides):
    base = {
        "chain": "arbitrum", "protocol": "uniswap_v3", "pool_address": "0xabc",
        "signal_type": "whale_entry", "confidence": 82, "z_score": 5.1,
        "total_volume_usd": 1_500_000.0, "detected_at": "2026-07-23T00:00:00+00:00",
        "id": None,
    }
    base.update(overrides)
    return base


class TestSignalFingerprint:
    def test_identical_signals_produce_identical_fingerprints(self):
        assert signal_fingerprint(_signal()) == signal_fingerprint(_signal())

    def test_different_signals_produce_different_fingerprints(self):
        assert signal_fingerprint(_signal()) != signal_fingerprint(_signal(confidence=61))

    def test_id_does_not_affect_fingerprint(self):
        """id is often None at this stage — must not be part of the identity."""
        assert signal_fingerprint(_signal(id=None)) == signal_fingerprint(_signal(id=42))


class TestKillSwitch:
    @pytest.mark.anyio
    async def test_inactive_by_default(self, redis_client):
        assert await is_kill_switch_active(redis_client) is False

    @pytest.mark.anyio
    async def test_active_once_set(self, redis_client):
        await redis_client.set(KILL_SWITCH_KEY, "1")
        assert await is_kill_switch_active(redis_client) is True

    @pytest.mark.anyio
    async def test_inactive_again_after_clearing(self, redis_client):
        await redis_client.set(KILL_SWITCH_KEY, "1")
        await redis_client.delete(KILL_SWITCH_KEY)
        assert await is_kill_switch_active(redis_client) is False


class TestSignalDedup:
    @pytest.mark.anyio
    async def test_first_sighting_is_not_a_duplicate(self, redis_client):
        assert await is_duplicate_signal(redis_client, _signal()) is False

    @pytest.mark.anyio
    async def test_second_sighting_of_same_signal_is_a_duplicate(self, redis_client):
        sig = _signal()
        assert await is_duplicate_signal(redis_client, sig) is False
        assert await is_duplicate_signal(redis_client, sig) is True

    @pytest.mark.anyio
    async def test_different_signals_are_independent(self, redis_client):
        assert await is_duplicate_signal(redis_client, _signal()) is False
        assert await is_duplicate_signal(redis_client, _signal(confidence=61)) is False

    @pytest.mark.anyio
    async def test_claim_is_atomic_under_concurrent_check(self, redis_client):
        """Two racing checks on the same signal must not both see 'not duplicate'."""
        import asyncio
        sig = _signal()
        results = await asyncio.gather(*[
            is_duplicate_signal(redis_client, sig) for _ in range(5)
        ])
        # Exactly one caller should have won the claim (seen False);
        # the rest must see True (already claimed).
        assert results.count(False) == 1
        assert results.count(True) == 4


class TestDailySpend:
    @pytest.mark.anyio
    async def test_starts_at_zero(self, redis_client):
        assert await get_daily_spent_usd(redis_client) == 0.0

    @pytest.mark.anyio
    async def test_records_and_accumulates(self, redis_client):
        await record_execution_spend(redis_client, 30.0)
        await record_execution_spend(redis_client, 15.5)
        assert await get_daily_spent_usd(redis_client) == 45.5

    @pytest.mark.anyio
    async def test_zero_amount_is_a_no_op(self, redis_client):
        await record_execution_spend(redis_client, 0.0)
        assert await get_daily_spent_usd(redis_client) == 0.0

    @pytest.mark.anyio
    async def test_sets_an_expiry(self, redis_client):
        from src.safety import _daily_key
        await record_execution_spend(redis_client, 10.0)
        ttl = await redis_client.ttl(_daily_key())
        assert ttl > 0
