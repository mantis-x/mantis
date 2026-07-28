"""HMAC webhook signing — no DB."""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.security.webhook_signing import (
    generate_webhook_secret, sign, verify_signature, SECRET_PREFIX,
)


def test_secret_shape():
    s = generate_webhook_secret()
    assert s.startswith(SECRET_PREFIX)
    assert generate_webhook_secret() != generate_webhook_secret()


def test_sign_verify_roundtrip():
    secret = generate_webhook_secret()
    body = '{"id":42,"chain":"ethereum"}'
    ts, sig = sign(secret, body)
    assert sig.startswith("sha256=")
    assert verify_signature(secret, body, ts, sig) is True


def test_tampered_body_fails():
    secret = generate_webhook_secret()
    ts, sig = sign(secret, '{"id":42}')
    assert verify_signature(secret, '{"id":43}', ts, sig) is False


def test_wrong_secret_fails():
    ts, sig = sign(generate_webhook_secret(), "body")
    assert verify_signature(generate_webhook_secret(), "body", ts, sig) is False


def test_stale_timestamp_rejected():
    secret = generate_webhook_secret()
    old = int(time.time()) - 10_000
    ts, sig = sign(secret, "body", timestamp=old)
    assert verify_signature(secret, "body", ts, sig, max_age_seconds=300) is False
