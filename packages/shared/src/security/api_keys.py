"""
API-key generation and hashing — the single source of truth for the key
format, shared by the mint script (scripts/mint_api_key.py) and the api
worker's auth layer (which mirrors this file, per this repo's
self-contained-package convention).

Format:  mantis_live_<43 url-safe base64 chars>   (32 bytes of entropy)
Stored:  key_hash  = sha256(plaintext) hex   — the only thing persisted
         key_prefix = first 20 chars          — display/debug only, not secret

The plaintext is returned exactly once (at mint) and never stored. Auth
hashes the presented key and matches key_hash directly, so a DB compromise
never yields usable keys.
"""
from __future__ import annotations

import hashlib
import secrets

KEY_ENV = "live"                       # "live" today; room for "test" later
KEY_PREFIX_STR = f"mantis_{KEY_ENV}_"
_PREFIX_DISPLAY_LEN = 20               # how much of the plaintext to keep for display


def hash_api_key(plaintext: str) -> str:
    """sha256 hex of the plaintext key — deterministic, one-way, indexable."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    """Return (plaintext, key_hash, key_prefix). Persist only the latter two."""
    plaintext = KEY_PREFIX_STR + secrets.token_urlsafe(32)
    return plaintext, hash_api_key(plaintext), plaintext[:_PREFIX_DISPLAY_LEN]
