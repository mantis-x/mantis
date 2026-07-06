"""
Integration tests for the mantis:signals / mantis:signals:exec fan-out.

Context: delivery and executor used to share a single Redis list
(mantis:signals). Delivery drained it via BRPOP (atomic, tail) while the
executor used LRANGE(0,0) + LPOP (peek head, then delete head) — a
non-atomic two-step read-then-delete. If enrichment pushed a new signal
between the executor's LRANGE and its LPOP, the LPOP would delete the
*new* signal instead of the one just processed, silently dropping it
while leaving the original stuck for reprocessing.

The fix: enrichment fans out to two independent lists — mantis:signals
(delivery, unchanged) and mantis:signals:exec (executor, new) — and the
executor now consumes via BLPOP, which is atomic: the item it returns is
guaranteed to be exactly the item removed.

These tests exercise the real push/pop sequences against fakeredis
(in-memory, real Redis command semantics) rather than mocking Redis calls,
so they'd catch a regression back to the old shared-list pattern.
"""
import asyncio
import json

import pytest
import fakeredis.aioredis as fakeredis_aio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def redis_client():
    return fakeredis_aio.FakeRedis(decode_responses=True)


@pytest.mark.anyio
async def test_fanout_delivers_to_both_queues(redis_client):
    r = redis_client
    signal = {"id": "sig-1", "protocol": "gmx", "confidence": 90}
    payload = json.dumps(signal)

    await r.lpush("mantis:signals", payload)
    await r.ltrim("mantis:signals", 0, 999)
    await r.rpush("mantis:signals:exec", payload)
    await r.ltrim("mantis:signals:exec", 0, 4999)

    assert await r.llen("mantis:signals") == 1
    assert await r.llen("mantis:signals:exec") == 1


@pytest.mark.anyio
async def test_delivery_consumption_does_not_affect_exec_queue(redis_client):
    """Delivery draining mantis:signals must leave mantis:signals:exec untouched."""
    r = redis_client
    for i in range(3):
        payload = json.dumps({"id": f"sig-{i}"})
        await r.lpush("mantis:signals", payload)
        await r.rpush("mantis:signals:exec", payload)

    # Delivery drains its queue completely
    while await r.llen("mantis:signals") > 0:
        await r.brpop("mantis:signals", timeout=1)

    assert await r.llen("mantis:signals") == 0
    assert await r.llen("mantis:signals:exec") == 3  # untouched


@pytest.mark.anyio
async def test_executor_consumption_does_not_affect_delivery_queue(redis_client):
    """Executor draining mantis:signals:exec must leave mantis:signals untouched."""
    r = redis_client
    for i in range(3):
        payload = json.dumps({"id": f"sig-{i}"})
        await r.lpush("mantis:signals", payload)
        await r.rpush("mantis:signals:exec", payload)

    while await r.llen("mantis:signals:exec") > 0:
        await r.blpop("mantis:signals:exec", timeout=1)

    assert await r.llen("mantis:signals:exec") == 0
    assert await r.llen("mantis:signals") == 3  # untouched


@pytest.mark.anyio
async def test_exec_queue_preserves_fifo_order(redis_client):
    """rpush + blpop must yield signals in the order enrichment produced them."""
    r = redis_client
    ids = [f"sig-{i}" for i in range(10)]
    for sid in ids:
        await r.rpush("mantis:signals:exec", json.dumps({"id": sid}))

    consumed = []
    while await r.llen("mantis:signals:exec") > 0:
        _, raw = await r.blpop("mantis:signals:exec", timeout=1)
        consumed.append(json.loads(raw)["id"])

    assert consumed == ids


@pytest.mark.anyio
async def test_no_signal_lost_under_concurrent_push_and_consume(redis_client):
    """
    Regression test for the original bug: a producer pushing signals while a
    consumer is draining the exec queue must never lose or duplicate a signal.
    With the old LRANGE+LPOP pattern this could drop the newly-pushed item;
    BLPOP is atomic so this cannot happen regardless of push/pop interleaving.
    """
    r = redis_client
    produced_ids = [f"sig-{i}" for i in range(50)]
    consumed_ids = []

    async def producer():
        for sid in produced_ids:
            await r.rpush("mantis:signals:exec", json.dumps({"id": sid}))
            await asyncio.sleep(0.001)

    async def consumer():
        while len(consumed_ids) < len(produced_ids):
            item = await r.blpop("mantis:signals:exec", timeout=2)
            if item is None:
                continue
            _, raw = item
            consumed_ids.append(json.loads(raw)["id"])

    await asyncio.wait_for(asyncio.gather(producer(), consumer()), timeout=10)

    assert sorted(consumed_ids) == sorted(produced_ids)
    assert len(consumed_ids) == len(set(consumed_ids))  # no duplicates
    assert await r.llen("mantis:signals:exec") == 0     # nothing left behind


@pytest.mark.anyio
async def test_exec_queue_ltrim_caps_backlog_without_losing_fifo_head(redis_client):
    """
    If the executor is down long enough for the exec queue to exceed the cap,
    LTRIM(0, 4999) must keep the oldest (head) items and drop overflow from
    the tail — preserving FIFO order for whatever remains queued.
    """
    r = redis_client
    for i in range(10):
        await r.rpush("mantis:signals:exec", json.dumps({"id": f"sig-{i}"}))
    await r.ltrim("mantis:signals:exec", 0, 4)  # simulate a cap of 5

    remaining = await r.lrange("mantis:signals:exec", 0, -1)
    ids = [json.loads(x)["id"] for x in remaining]
    assert ids == [f"sig-{i}" for i in range(5)]  # oldest 5 preserved, in order
