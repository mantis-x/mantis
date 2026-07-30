"""Access-request (lead capture) endpoint — no DB needed; fakeredis, no Telegram."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fakeredis
import pytest

import src.ratelimit as ratelimit


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch):
    ratelimit._client = fakeredis.FakeStrictRedis(decode_responses=True)
    # ensure no admin notify is attempted
    monkeypatch.delenv("ADMIN_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    yield
    ratelimit._client = None


def _client():
    from fastapi.testclient import TestClient
    from src.main import create_app
    return TestClient(create_app())


def test_valid_request_stored_and_ok():
    c = _client()
    r = c.post("/v1/access-request", json={"email": "fund@acme.xyz", "wallet": "0x" + "a" * 40})
    assert r.status_code == 200 and r.json()["ok"] is True
    leads = ratelimit._client.lrange("mantis:access_requests", 0, -1)
    assert len(leads) == 1
    d = json.loads(leads[0])
    assert d["email"] == "fund@acme.xyz" and d["wallet"] == "0x" + "a" * 40


def test_bad_email_400():
    assert _client().post("/v1/access-request", json={"email": "not-an-email"}).status_code == 400


def test_bad_wallet_400():
    r = _client().post("/v1/access-request", json={"email": "a@b.co", "wallet": "0x123"})
    assert r.status_code == 400


def test_wallet_optional():
    assert _client().post("/v1/access-request", json={"email": "a@b.co"}).status_code == 200


def test_rate_limited_per_ip():
    c = _client()
    # default limit 5 per window; 6th from same IP → 429
    hdr = {"X-Forwarded-For": "203.0.113.9"}
    codes = [c.post("/v1/access-request", json={"email": f"u{i}@b.co"}, headers=hdr).status_code
             for i in range(6)]
    assert codes[:5] == [200] * 5
    assert codes[5] == 429


def test_cors_header_present_for_allowed_origin():
    c = _client()
    r = c.post("/v1/access-request", json={"email": "a@b.co"},
               headers={"Origin": "https://mantis.baiq.tech"})
    assert r.headers.get("access-control-allow-origin") == "https://mantis.baiq.tech"
