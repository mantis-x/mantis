"""Billing endpoints — register wallet, get pricing/status, tenancy. Postgres + fakeredis."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fakeredis
import pytest

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://mantis:pw@localhost:5433/mantis")
os.environ["DATABASE_URL"] = DATABASE_URL
os.environ["API_TIER_ENABLED"] = "true"

import src.ratelimit as ratelimit
from src.db.connection import get_engine, get_session, reset_engine_for_tests
from src.db.models import Base, ApiCustomerRow, ApiKeyRow
from src.security.api_keys import generate_api_key


def _pg():
    try:
        reset_engine_for_tests()
        with get_engine().connect():
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg(), reason="no Postgres reachable")


@pytest.fixture(autouse=True)
def _fake_redis():
    ratelimit._client = fakeredis.FakeStrictRedis(decode_responses=True)
    yield
    ratelimit._client = None


@pytest.fixture()
def db():
    reset_engine_for_tests()
    os.environ["DATABASE_URL"] = DATABASE_URL
    eng = get_engine()
    Base.metadata.drop_all(eng); Base.metadata.create_all(eng)
    yield
    Base.metadata.drop_all(eng)


def _key():
    plain, h, pfx = generate_api_key()
    with get_session() as s:
        c = ApiCustomerRow(label="co"); s.add(c); s.flush()
        s.add(ApiKeyRow(customer_id=c.id, key_hash=h, key_prefix=pfx, rate_limit_per_min=1000))
        s.flush()
        cid = c.id
    return plain, cid


def _client():
    from fastapi.testclient import TestClient
    from src.main import create_app
    return TestClient(create_app())


def _h(k):
    return {"Authorization": f"Bearer {k}"}


def test_billing_info_shows_tiers(db):
    key, _ = _key()
    r = _client().get("/v1/billing", headers=_h(key))
    assert r.status_code == 200
    body = r.json()
    assert body["currency"] == "USDC" and body["chain"] == "arbitrum"
    months = {t["months"] for t in body["tiers"]}
    assert months == {1, 6, 12}
    assert body["registered_wallet"] is None
    assert body["active"] is True          # comped (no expiry) customer


def test_register_wallet_persists_lowercased(db):
    key, cid = _key()
    c = _client()
    addr = "0xAbCdef0000000000000000000000000000001234"
    r = c.post("/v1/billing/wallet", headers=_h(key), json={"address": addr})
    assert r.status_code == 200
    assert r.json()["registered_wallet"] == addr.lower()
    with get_session() as s:
        assert s.get(ApiCustomerRow, cid).registered_wallet == addr.lower()


def test_register_wallet_rejects_bad_address(db):
    key, _ = _key()
    r = _client().post("/v1/billing/wallet", headers=_h(key), json={"address": "not-an-address"})
    assert r.status_code == 400


def test_billing_scoped_per_customer(db):
    key_a, _ = _key()
    key_b, _ = _key()
    c = _client()
    c.post("/v1/billing/wallet", headers=_h(key_a), json={"address": "0x" + "a" * 40})
    # B's billing info must not show A's wallet
    assert c.get("/v1/billing", headers=_h(key_b)).json()["registered_wallet"] is None


def test_billing_404_when_tier_disabled(db, monkeypatch):
    key, _ = _key()
    monkeypatch.setenv("API_TIER_ENABLED", "false")
    assert _client().get("/v1/billing", headers=_h(key)).status_code == 404
