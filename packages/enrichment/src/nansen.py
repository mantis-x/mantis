"""
Nansen wallet-labeling client — best-effort smart-money detection.

Optional: no-ops entirely if NANSEN_API_KEY is unset (same "disabled without
its token, never a hard dependency" pattern as Discord/LINE elsewhere in this
codebase). Labels feed into the enrichment prompt and the delivered Signal so
a user sees "3 wallets, 2 smart money" instead of just a bare wallet count.

VERIFY BEFORE ENABLING IN PRODUCTION: the request/response shape below
(endpoint path, `apiKey` header, `address`/`chain` body fields, `data[].category
== "smart_money"` response field) comes from Nansen's public docs, not a real
call against a live key — nobody has exercised this against api.nansen.ai yet.
Because is_smart_money() catches all request exceptions and returns None (by
design, so a lookup failure never blocks a signal from being enriched/delivered),
a wrong field name or auth scheme fails *silently* — every wallet just looks
"unlabeled" forever, with only a per-call log.warning to notice by. This is
exactly the failure shape that has bitten this project three times already
(retired model, exhausted billing, ThinkingBlock/.text crash — see
PROJECT_STATE.md). Before setting NANSEN_API_KEY in Railway, run one real
lookup locally against a wallet you already know Nansen labels smart_money
and confirm `is_smart_money()` returns True, not just that it doesn't crash.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import requests

log = logging.getLogger(__name__)

API_URL = "https://api.nansen.ai/api/v1/profiler/address/labels"
TIMEOUT_SECONDS = 3.0

# Chains Nansen's labels endpoint supports today. HashKey isn't one of them —
# skip the call rather than spend a request on a guaranteed miss.
SUPPORTED_CHAINS = {
    "ethereum", "arbitrum", "mantle", "polygon", "optimism", "base",
    "bnb", "avalanche", "linea", "sonic", "solana",
}


class NansenClient:
    """Looks up whether a wallet carries Nansen's `smart_money` label."""

    def __init__(self, api_key: str = ""):
        self._api_key = api_key or os.getenv("NANSEN_API_KEY", "")
        self._cache: dict[tuple[str, str], Optional[bool]] = {}

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def is_smart_money(self, address: str, chain: str) -> Optional[bool]:
        """
        True/False if a label lookup actually succeeded; None if it couldn't be
        made (no API key, unsupported chain, or the call failed) — callers
        must treat None as "unknown", never coerce it to "not smart money".
        """
        if not self.is_configured or chain not in SUPPORTED_CHAINS:
            return None

        key = (chain, address.lower())
        if key in self._cache:
            return self._cache[key]

        try:
            resp = requests.post(
                API_URL,
                headers={"apiKey": self._api_key},
                json={"address": address, "chain": chain},
                timeout=TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            labels = resp.json().get("data", [])
        except Exception as exc:
            log.warning("Nansen label lookup failed for %s: %s", address, exc)
            return None

        smart = any(label.get("category") == "smart_money" for label in labels)
        self._cache[key] = smart
        return smart
