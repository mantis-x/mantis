"""
Webhook dispatcher — state-machine unit tests (no DB) + delivery/idempotency/
retry integration (Postgres). Skips if no Postgres reachable.
"""
import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timedelta, timezone

import pytest

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://mantis:pw@localhost:5433/mantis")
os.environ["DATABASE_URL"] = DATABASE_URL

from src.db.connection import get_engine, get_session, reset_engine_for_tests
from src.db.models import Base, ApiCustomerRow, WebhookRow, WebhookDeliveryRow, DeliveryStatus, SignalRow
from src.webhook_dispatcher import (
    WebhookDispatcher, matches, MAX_ATTEMPTS, DISABLE_THRESHOLD,
)

NOW = datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc)


# ── pure filter matching (no DB) ────────────────────────────────────────────
def test_matches_no_filters():
    assert matches(None, {"chain": "x"}) is True
    assert matches({}, {"chain": "x"}) is True


def test_matches_chain_and_confidence():
    sig = {"chain": "ethereum", "signal_type": "accumulation", "confidence": 80}
    assert matches({"chain": "ethereum"}, sig) is True
    assert matches({"chain": "arbitrum"}, sig) is False
    assert matches({"min_confidence": 70}, sig) is True
    assert matches({"min_confidence": 90}, sig) is False
    assert matches({"signal_type": "whale_exit"}, sig) is False


# ── state machine (_apply_result) on detached rows (no DB) ───────────────────
def _wh(**kw):
    defaults = dict(url="https://x", secret="whsec_x", active=True, consecutive_failures=0)
    defaults.update(kw)
    return WebhookRow(**defaults)


def _dl(**kw):
    defaults = dict(webhook_id=1, signal_id=1, status=DeliveryStatus.PENDING, attempts=0)
    defaults.update(kw)
    return WebhookDeliveryRow(**defaults)


def test_success_marks_delivered_and_resets_streak():
    d = WebhookDispatcher(sender=None)
    wh = _wh(consecutive_failures=4)
    dl = _dl()
    d._apply_result(dl, wh, ok=True, status=200)
    assert dl.status == DeliveryStatus.DELIVERED
    assert dl.attempts == 1
    assert wh.consecutive_failures == 0


def test_single_failure_schedules_retry():
    d = WebhookDispatcher(sender=None)
    wh = _wh()
    dl = _dl()
    d._apply_result(dl, wh, ok=False, status=500)
    assert dl.status == DeliveryStatus.FAILED
    assert dl.attempts == 1
    assert dl.next_retry_at is not None
    assert wh.consecutive_failures == 0     # only bumped on exhaustion


def test_exhaustion_bumps_streak():
    d = WebhookDispatcher(sender=None)
    wh = _wh()
    dl = _dl(attempts=MAX_ATTEMPTS - 1)
    d._apply_result(dl, wh, ok=False, status=500)
    assert dl.status == DeliveryStatus.EXHAUSTED
    assert wh.consecutive_failures == 1


def test_auto_disable_at_threshold():
    d = WebhookDispatcher(sender=None)
    wh = _wh(consecutive_failures=DISABLE_THRESHOLD - 1)
    dl = _dl(attempts=MAX_ATTEMPTS - 1)
    d._apply_result(dl, wh, ok=False, status=500)
    assert wh.consecutive_failures == DISABLE_THRESHOLD
    assert wh.active is False
    assert wh.disabled_at is not None


# ── integration (Postgres) ──────────────────────────────────────────────────
def _pg_available():
    try:
        reset_engine_for_tests()
        with get_engine().connect():
            return True
    except Exception:
        return False


pg = pytest.mark.skipif(not _pg_available(), reason="no Postgres reachable")


class FakeSender:
    def __init__(self, result=(True, 200)):
        self.result = result
        self.calls = []

    async def __call__(self, url, headers, body):
        self.calls.append((url, headers, body))
        return self.result


@pytest.fixture()
def db():
    reset_engine_for_tests()
    os.environ["DATABASE_URL"] = DATABASE_URL
    eng = get_engine()
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield
    Base.metadata.drop_all(eng)


def _customer():
    with get_session() as s:
        c = ApiCustomerRow(label="c"); s.add(c); s.flush()
        return c.id


def _webhook(customer_id, filters=None, active=True):
    with get_session() as s:
        w = WebhookRow(customer_id=customer_id, url="https://hook.example.com",
                       secret="whsec_test", active=active, event_filters=filters)
        s.add(w); s.flush()
        return w.id


def _sig(sid=1, chain="ethereum", stype="accumulation", conf=80):
    return {"id": sid, "chain": chain, "signal_type": stype, "confidence": conf,
            "pool_address": "0xp", "protocol": "uniswap_v3", "total_volume_usd": 5e5}


@pg
def test_deliver_matching_active_only(db):
    cid = _customer()
    w_eth = _webhook(cid, filters={"chain": "ethereum"})
    w_arb = _webhook(cid, filters={"chain": "arbitrum"})
    _webhook(cid, active=False)     # inactive → never delivered
    sender = FakeSender((True, 200))
    n = asyncio.run(WebhookDispatcher(sender=sender).deliver_signal(_sig(chain="ethereum")))
    assert n == 1 and len(sender.calls) == 1
    with get_session() as s:
        rows = s.query(WebhookDeliveryRow).all()
        assert len(rows) == 1
        assert rows[0].webhook_id == w_eth and rows[0].status == DeliveryStatus.DELIVERED


@pg
def test_idempotent_no_double_delivery(db):
    cid = _customer()
    _webhook(cid)
    sender = FakeSender((True, 200))
    disp = WebhookDispatcher(sender=sender)
    asyncio.run(disp.deliver_signal(_sig(sid=7)))
    asyncio.run(disp.deliver_signal(_sig(sid=7)))    # same signal again
    assert len(sender.calls) == 1                    # not re-sent
    with get_session() as s:
        assert s.query(WebhookDeliveryRow).count() == 1


@pg
def test_failure_then_retry_due(db):
    cid = _customer()
    _webhook(cid)
    # persist the signal row so retry_due can rebuild the body from DB
    with get_session() as s:
        s.add(SignalRow(id=9, chain="ethereum", protocol="uniswap_v3", pool_address="0xp",
                        wallets=[], signal_type="accumulation", confidence=80,
                        summary="x", key_factors=[], detected_at=NOW, deliver_at=NOW,
                        z_score=4.0, total_volume_usd=5e5, event_type="swap"))
        s.flush()
    # first attempt fails
    asyncio.run(WebhookDispatcher(sender=FakeSender((False, 500))).deliver_signal(_sig(sid=9)))
    with get_session() as s:
        d = s.query(WebhookDeliveryRow).one()
        assert d.status == DeliveryStatus.FAILED and d.attempts == 1
        # force it due relative to the REAL clock (retry_due compares to now())
        d.next_retry_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    # retry succeeds
    ok_sender = FakeSender((True, 200))
    retried = asyncio.run(WebhookDispatcher(sender=ok_sender).retry_due())
    assert retried == 1 and len(ok_sender.calls) == 1
    with get_session() as s:
        assert s.query(WebhookDeliveryRow).one().status == DeliveryStatus.DELIVERED
