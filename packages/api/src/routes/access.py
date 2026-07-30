"""
Access-request (lead capture) — a public, unauthenticated endpoint the marketing
site's "Get API access" form POSTs to. There's no self-serve key issuance yet
(that's a larger build + the legal/ToS gate), so this just captures the lead:
stores it durably in Redis and best-effort pings the operator on Telegram, who
then hand-mints a key with scripts/mint_api_key.py.

Abuse-guarded: per-IP rate limit (real client IP from X-Forwarded-For behind
Railway's proxy). Stores no secrets; email/wallet are the lead's own contact info.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

import requests
from fastapi import APIRouter, HTTPException, Request, status

from src import ratelimit
from src.schemas import AccessRequestIn, AccessResultOut

log = logging.getLogger("mantis.api.access")
router = APIRouter()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

_LEADS_KEY = "mantis:access_requests"
_RATE_LIMIT = int(os.getenv("ACCESS_REQUEST_RATE_LIMIT", "5"))        # per IP
_RATE_WINDOW_S = int(os.getenv("ACCESS_REQUEST_RATE_WINDOW_S", "600"))  # 10 min


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _notify_admin(email: str, wallet: str | None, note: str | None) -> None:
    """Best-effort Telegram ping to the operator. Silent no-op if unconfigured."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("ADMIN_TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    text = (
        "🔑 New Mantis API access request\n"
        f"email: {email}\n"
        f"wallet: {wallet or '—'}\n"
        f"note: {note or '—'}"
    )
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text},
            timeout=8,
        )
    except Exception as exc:  # noqa: BLE001 — notify is best-effort; lead is already stored
        log.warning("Admin notify failed (lead still stored): %s", exc)


@router.post("/v1/access-request", response_model=AccessResultOut, tags=["access"])
def access_request(body: AccessRequestIn, request: Request) -> AccessResultOut:
    ip = _client_ip(request)
    if not ratelimit.check_rate_limit_key(f"access:{ip}", _RATE_LIMIT, _RATE_WINDOW_S):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests — try again later.",
            headers={"Retry-After": str(_RATE_WINDOW_S)},
        )

    email = (body.email or "").strip()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A valid email is required.")

    wallet = (body.wallet or "").strip() or None
    if wallet and not _ADDRESS_RE.match(wallet):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="wallet must be a 0x-prefixed 40-hex-char address, or omitted.")

    note = (body.note or "").strip()[:500] or None

    lead = {
        "email": email, "wallet": wallet, "note": note,
        "ip": ip, "ts": int(time.time()),
    }
    # Durable backup so a lead is never lost even if the Telegram ping fails.
    try:
        r = ratelimit.client()
        r.lpush(_LEADS_KEY, json.dumps(lead))
        r.ltrim(_LEADS_KEY, 0, 4999)
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to store access-request lead in Redis: %s", exc)

    _notify_admin(email, wallet, note)
    log.info("Access request received: email=%s wallet=%s", email, wallet or "—")
    return AccessResultOut(ok=True, message="Thanks — we'll be in touch with your API key shortly.")
