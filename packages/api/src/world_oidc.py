"""World ID Agents OIDC client and signed approval artifacts."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Mapping
from urllib.parse import urlencode

import jwt
import requests


class WorldOIDCError(Exception):
    pass


def _issuer() -> str:
    return os.getenv("WORLD_ID_ISSUER", "https://sandbox.auth.world.org").rstrip("/")


def _secret() -> bytes:
    value = os.getenv("WORLD_ID_STATE_SECRET", "")
    if not value:
        raise WorldOIDCError("WORLD_ID_STATE_SECRET is not configured")
    return value.encode()


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _pkce_verifier(nonce: str) -> str:
    """Derive a per-request verifier without putting it in the browser state."""
    return _b64(hmac.new(_secret(), f"pkce:{nonce}".encode(), hashlib.sha256).digest())


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(payload: Mapping[str, Any]) -> str:
    encoded = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    signature = hmac.new(_secret(), encoded.encode(), hashlib.sha256).digest()
    return f"{encoded}.{_b64(signature)}"


def _read_signed(value: str) -> dict[str, Any]:
    try:
        encoded, supplied = value.split(".", 1)
        expected = _b64(hmac.new(_secret(), encoded.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(supplied, expected):
            raise WorldOIDCError("invalid World state signature")
        payload = json.loads(_unb64(encoded))
        if not isinstance(payload, dict) or int(payload.get("exp", 0)) < int(time.time()):
            raise WorldOIDCError("expired World state")
        return payload
    except (ValueError, TypeError, binascii.Error, json.JSONDecodeError) as exc:
        raise WorldOIDCError("invalid World state") from exc


def _discover() -> dict[str, Any]:
    response = requests.get(f"{_issuer()}/.well-known/openid-configuration", timeout=10)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise WorldOIDCError("World discovery response is not an object")
    return data


def authorization_url(intent_hash: str) -> str:
    client_id = os.getenv("WORLD_ID_CLIENT_ID", "")
    redirect_uri = os.getenv("WORLD_ID_REDIRECT_URI", "")
    if not client_id or not redirect_uri:
        raise WorldOIDCError("World OIDC client ID and redirect URI are required")
    nonce = secrets.token_urlsafe(24)
    state = _sign({"intent_hash": intent_hash, "nonce": nonce, "exp": int(time.time()) + 600})
    verifier = _pkce_verifier(nonce)
    metadata = _discover()
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "openid",
        "state": state,
        "nonce": nonce,
        "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()),
        "code_challenge_method": "S256",
    }
    return f"{metadata['authorization_endpoint']}?{urlencode(params)}"


def exchange_and_issue_artifact(code: str, state: str) -> dict[str, Any]:
    state_data = _read_signed(state)
    client_id = os.getenv("WORLD_ID_CLIENT_ID", "")
    client_secret = os.getenv("WORLD_ID_CLIENT_SECRET", "")
    redirect_uri = os.getenv("WORLD_ID_REDIRECT_URI", "")
    if not client_id or not client_secret or not redirect_uri:
        raise WorldOIDCError("World OIDC credentials are incomplete")

    metadata = _discover()
    response = requests.post(
        metadata["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": _pkce_verifier(state_data["nonce"]),
        },
        auth=(client_id, client_secret),
        headers={"Accept": "application/json"},
        timeout=10,
    )
    response.raise_for_status()
    tokens = response.json()
    id_token = tokens.get("id_token") if isinstance(tokens, dict) else None
    if not id_token:
        raise WorldOIDCError("World token response did not contain an ID token")

    signing_key = jwt.PyJWKClient(metadata["jwks_uri"]).get_signing_key_from_jwt(id_token)
    claims = jwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256"],
        audience=client_id,
        issuer=_issuer(),
        options={"require": ["sub", "iss", "aud", "exp"]},
    )
    if claims.get("nonce") != state_data["nonce"]:
        raise WorldOIDCError("World ID nonce mismatch")

    artifact = _sign({
        "sub": claims["sub"],
        "intent_hash": state_data["intent_hash"],
        "iat": int(time.time()),
        "exp": int(time.time()) + int(os.getenv("WORLD_ID_ARTIFACT_TTL_SECONDS", "600")),
    })
    return {"artifact": artifact, "subject": claims["sub"], "intent_hash": state_data["intent_hash"]}
