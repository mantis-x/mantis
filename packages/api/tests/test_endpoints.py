"""
Integration tests for the api worker — FastAPI TestClient against a real
disposable Postgres (JSONB requires Postgres, not SQLite) + fakeredis for
rate limiting. Skips cleanly if no Postgres is reachable, matching this
repo's "DB-dependent tests need a live Postgres" convention.

Run with a disposable PG, e.g.:
    docker run -d --rm -p 5433:5432 -e POSTGRES_PASSWORD=pw -e POSTGRES_USER=mantis \
        -e POSTGRES_DB=mantis postgres:16-alpine
    DATABASE_URL=postgresql://mantis:pw@localhost:5433/mantis \
        python -m pytest packages/api/tests/test_endpoints.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timedelta, timezone

import fakeredis
import pytest

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://mantis:pw@localhost:5433/mantis")
os.environ["DATABASE_URL"] = DATABASE_URL

import src.ratelimit as ratelimit
from src.db.connection import get_engine, get_session, reset_engine_for_tests
from src.db.models import Base, ApiCustomerRow, ApiKeyRow, SignalRow, SignalOutcomeRow
from src.security.api_keys import generate_api_key


def _pg_available() -> bool:
    try:
        reset_engine_for_tests()
        eng = get_engine()
        with eng.connect():
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_available(), reason="no Postgres reachable at DATABASE_URL")


NOW = datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch):
    """Point the rate limiter at fakeredis so tests don't need a real Redis."""
    ratelimit._client = fakeredis.FakeStrictRedis(decode_responses=True)
    yield
    ratelimit._client = None


@pytest.fixture()
def db():
    reset_engine_for_tests()
    os.environ["DATABASE_URL"] = DATABASE_URL
    eng = get_engine()
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield
    Base.metadata.drop_all(eng)


def _seed_key(expires_at="active", rate_limit=60, revoked=False):
    """Create a customer + key; return the plaintext. expires_at: 'active' (future),
    'expired' (past), or None (comped/never)."""
    plaintext, key_hash, prefix = generate_api_key()
    with get_session() as s:
        exp = None
        if expires_at == "active":
            exp = NOW + timedelta(days=30)
        elif expires_at == "expired":
            exp = datetime.now(tz=timezone.utc) - timedelta(days=1)
        cust = ApiCustomerRow(label="Test Co", api_tier_expires_at=exp)
        s.add(cust); s.flush()
        key = ApiKeyRow(
            customer_id=cust.id, key_hash=key_hash, key_prefix=prefix,
            rate_limit_per_min=rate_limit,
            revoked_at=(datetime.now(tz=timezone.utc) if revoked else None),
        )
        s.add(key); s.flush()
    return plaintext


def _seed_signal(sid_chain="ethereum", signal_type="accumulation", confidence=80,
                 pct_change_24h=5.0):
    with get_session() as s:
        row = SignalRow(
            chain=sid_chain, protocol="uniswap_v3", pool_address="0xpool",
            wallets=["0xaaa"], signal_type=signal_type, confidence=confidence,
            summary="test", key_factors=["a"], detected_at=NOW, deliver_at=NOW,
            z_score=4.0, total_volume_usd=500000.0, event_type="swap",
        )
        s.add(row); s.flush()
        if pct_change_24h is not None:
            s.add(SignalOutcomeRow(
                signal_id=row.id, horizon_label="24h", due_at=NOW + timedelta(hours=24),
                price_key="eth", entry_price_usd=3000.0, status="completed",
                price_usd=3000.0 * (1 + pct_change_24h / 100), pct_change=pct_change_24h,
                checked_at=NOW + timedelta(hours=24),
            ))
            s.flush()
        return row.id


def _client(enabled=True):
    if enabled:
        os.environ["API_TIER_ENABLED"] = "true"
    else:
        os.environ.pop("API_TIER_ENABLED", None)
    from fastapi.testclient import TestClient
    from src.main import create_app
    return TestClient(create_app())


# ── health (always up) ────────────────────────────────────────────────────
def test_health_up_when_disabled(db):
    c = _client(enabled=False)
    r = c.get("/v1/health")
    assert r.status_code == 200
    assert r.json()["api_tier_enabled"] is False


# ── gate ──────────────────────────────────────────────────────────────────
def test_signals_404_when_tier_disabled(db):
    key = _seed_key()
    c = _client(enabled=False)
    r = c.get("/v1/signals", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 404


# ── auth ──────────────────────────────────────────────────────────────────
def test_missing_key_401(db):
    c = _client()
    assert c.get("/v1/signals").status_code == 401


def test_bad_key_401(db):
    _seed_key()
    c = _client()
    assert c.get("/v1/signals", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_revoked_key_401(db):
    key = _seed_key(revoked=True)
    c = _client()
    assert c.get("/v1/signals", headers={"Authorization": f"Bearer {key}"}).status_code == 401


def test_expired_customer_403(db):
    key = _seed_key(expires_at="expired")
    c = _client()
    assert c.get("/v1/signals", headers={"Authorization": f"Bearer {key}"}).status_code == 403


def test_comped_key_works(db):
    key = _seed_key(expires_at=None)
    _seed_signal()
    c = _client()
    assert c.get("/v1/signals", headers={"Authorization": f"Bearer {key}"}).status_code == 200


# ── feed ──────────────────────────────────────────────────────────────────
def test_list_and_filters(db):
    key = _seed_key()
    _seed_signal(sid_chain="ethereum", confidence=80)
    _seed_signal(sid_chain="arbitrum", confidence=55)
    c = _client()
    h = {"Authorization": f"Bearer {key}"}

    allsig = c.get("/v1/signals", headers=h).json()
    assert len(allsig["data"]) == 2

    eth = c.get("/v1/signals?chain=ethereum", headers=h).json()
    assert len(eth["data"]) == 1 and eth["data"][0]["chain"] == "ethereum"

    hi = c.get("/v1/signals?min_confidence=70", headers=h).json()
    assert len(hi["data"]) == 1 and hi["data"][0]["confidence"] == 80


def test_pagination_cursor(db):
    key = _seed_key()
    ids = [_seed_signal() for _ in range(3)]
    c = _client()
    h = {"Authorization": f"Bearer {key}"}

    page1 = c.get("/v1/signals?limit=2", headers=h).json()
    assert len(page1["data"]) == 2 and page1["next_cursor"] is not None
    page2 = c.get(f"/v1/signals?limit=2&cursor={page1['next_cursor']}", headers=h).json()
    assert len(page2["data"]) == 1 and page2["next_cursor"] is None
    seen = [s["id"] for s in page1["data"] + page2["data"]]
    assert sorted(seen) == sorted(ids)


def test_get_by_id_and_404(db):
    key = _seed_key()
    sid = _seed_signal(pct_change_24h=6.0)
    c = _client()
    h = {"Authorization": f"Bearer {key}"}

    r = c.get(f"/v1/signals/{sid}", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == sid
    assert len(body["outcomes"]) == 1 and body["outcomes"][0]["horizon"] == "24h"

    assert c.get("/v1/signals/999999", headers=h).status_code == 404


def test_stats_hit_rate(db):
    key = _seed_key()
    # 2 ethereum accumulation (up): one +5% (hit), one -3% (miss) → hit_rate 0.5
    _seed_signal(sid_chain="ethereum", signal_type="accumulation", pct_change_24h=5.0)
    _seed_signal(sid_chain="ethereum", signal_type="accumulation", pct_change_24h=-3.0)
    # a non-directional type → excluded from hit rate
    _seed_signal(sid_chain="ethereum", signal_type="unusual_volume", pct_change_24h=9.0)
    c = _client()
    r = c.get("/v1/stats", headers={"Authorization": f"Bearer {key}"}).json()
    assert r["total_signals"] == 3
    eth = next(x for x in r["by_chain"] if x["chain"] == "ethereum")
    assert eth["signals"] == 3
    assert eth["resolved"] == 2          # only the two directional ones
    assert eth["hit_rate"] == 0.5


# ── rate limit ────────────────────────────────────────────────────────────
def test_rate_limit_429(db):
    key = _seed_key(rate_limit=2)
    _seed_signal()
    c = _client()
    h = {"Authorization": f"Bearer {key}"}
    assert c.get("/v1/signals", headers=h).status_code == 200
    assert c.get("/v1/signals", headers=h).status_code == 200
    assert c.get("/v1/signals", headers=h).status_code == 429
