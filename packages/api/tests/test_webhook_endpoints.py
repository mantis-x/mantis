"""
Webhook management endpoints — auth, tenancy/IDOR, SSRF guard, register/list/
delete/test. Postgres + fakeredis; DNS and outbound POST are monkeypatched so
the suite is hermetic. Skips if no Postgres.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fakeredis
import pytest

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://mantis:pw@localhost:5433/mantis")
os.environ["DATABASE_URL"] = DATABASE_URL
os.environ["API_TIER_ENABLED"] = "true"

import src.ratelimit as ratelimit
import src.url_guard as url_guard
from src.db.connection import get_engine, get_session, reset_engine_for_tests
from src.db.models import Base, ApiCustomerRow, ApiKeyRow, WebhookRow
from src.security.api_keys import generate_api_key


def _pg_available():
    try:
        reset_engine_for_tests()
        with get_engine().connect():
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_available(), reason="no Postgres reachable")


@pytest.fixture(autouse=True)
def _fakes(monkeypatch):
    ratelimit._client = fakeredis.FakeStrictRedis(decode_responses=True)
    # DNS → public IP by default (happy path); individual tests override.
    monkeypatch.setattr(url_guard.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
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


def _h(key):
    return {"Authorization": f"Bearer {key}"}


def test_register_returns_secret_once(db):
    key, _ = _key()
    c = _client()
    r = c.post("/v1/webhooks", headers=_h(key),
               json={"url": "https://hook.example.com/mantis", "event_filters": {"chain": "ethereum"}})
    assert r.status_code == 201
    body = r.json()
    assert body["secret"].startswith("whsec_")
    assert body["url"] == "https://hook.example.com/mantis"
    assert body["event_filters"] == {"chain": "ethereum"}


def test_register_rejects_http(db):
    key, _ = _key()
    r = _client().post("/v1/webhooks", headers=_h(key), json={"url": "http://hook.example.com"})
    assert r.status_code == 400


def test_register_rejects_private_ip(db, monkeypatch):
    monkeypatch.setattr(url_guard.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))])
    key, _ = _key()
    r = _client().post("/v1/webhooks", headers=_h(key), json={"url": "https://evil.example.com"})
    assert r.status_code == 400
    assert "non-public" in r.json()["detail"]


def test_list_scoped_to_own_customer(db):
    key_a, _ = _key()
    key_b, _ = _key()
    c = _client()
    c.post("/v1/webhooks", headers=_h(key_a), json={"url": "https://a.example.com"})
    assert len(c.get("/v1/webhooks", headers=_h(key_a)).json()) == 1
    assert c.get("/v1/webhooks", headers=_h(key_b)).json() == []     # B sees nothing of A's


def test_delete_others_webhook_is_404(db):
    key_a, _ = _key()
    key_b, _ = _key()
    c = _client()
    wid = c.post("/v1/webhooks", headers=_h(key_a), json={"url": "https://a.example.com"}).json()["id"]
    # B cannot delete A's webhook
    assert c.delete(f"/v1/webhooks/{wid}", headers=_h(key_b)).status_code == 404
    # A can
    assert c.delete(f"/v1/webhooks/{wid}", headers=_h(key_a)).status_code == 204
    assert c.get("/v1/webhooks", headers=_h(key_a)).json() == []


def test_test_endpoint_signs_and_reports(db, monkeypatch):
    key, _ = _key()
    c = _client()
    wid = c.post("/v1/webhooks", headers=_h(key), json={"url": "https://a.example.com"}).json()["id"]

    captured = {}

    class FakeResp:
        status_code = 200

    def fake_post(url, data=None, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return FakeResp()

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    r = c.post(f"/v1/webhooks/{wid}/test", headers=_h(key))
    assert r.status_code == 200 and r.json()["delivered"] is True
    assert "X-Mantis-Signature" in captured["headers"]


def test_webhook_routes_404_when_tier_disabled(db, monkeypatch):
    key, _ = _key()
    monkeypatch.setenv("API_TIER_ENABLED", "false")
    r = _client().get("/v1/webhooks", headers=_h(key))
    assert r.status_code == 404
