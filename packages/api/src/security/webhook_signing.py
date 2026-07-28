"""
Webhook HMAC signing — the source-of-truth for how the API tier signs webhook
deliveries, shared by the dispatcher (which signs) and mirrored into any code
that verifies. Convention follows Stripe/GitHub so it's familiar to customers.

Each delivery carries two headers:
    X-Mantis-Timestamp: <unix seconds>
    X-Mantis-Signature: sha256=<hex hmac>

The signature is HMAC-SHA256 over the exact string  f"{timestamp}.{raw_body}"
using the webhook's signing secret. The timestamp is included in the signed
content so a captured payload can't be replayed with a fresh timestamp; a
verifier should also reject timestamps outside a tolerance window (see
verify_signature's max_age_seconds).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

SECRET_PREFIX = "whsec_"
SIG_HEADER = "X-Mantis-Signature"
TS_HEADER = "X-Mantis-Timestamp"


def generate_webhook_secret() -> str:
    return SECRET_PREFIX + secrets.token_urlsafe(32)


def sign(secret: str, body: str, timestamp: int | None = None) -> tuple[str, str]:
    """Return (timestamp_str, signature_header_value) for a raw JSON body."""
    ts = str(timestamp if timestamp is not None else int(time.time()))
    mac = hmac.new(secret.encode("utf-8"), f"{ts}.{body}".encode("utf-8"), hashlib.sha256)
    return ts, f"sha256={mac.hexdigest()}"


def verify_signature(
    secret: str, body: str, timestamp: str, signature: str, max_age_seconds: int = 300
) -> bool:
    """Reference verifier (also used by tests). Constant-time compare; rejects
    stale timestamps to blunt replay."""
    try:
        ts_int = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(int(time.time()) - ts_int) > max_age_seconds:
        return False
    _, expected = sign(secret, body, timestamp=ts_int)
    return hmac.compare_digest(expected, signature or "")
