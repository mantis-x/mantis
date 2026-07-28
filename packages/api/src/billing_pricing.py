"""
API-tier pricing for the GET /v1/billing display — mirror of
packages/shared/src/billing/api_payment_watcher.py's api_pricing_tiers()
(the watcher owns crediting; this only shows customers the same numbers).
Keep the price/discount env reads in sync with that module.
"""
from __future__ import annotations

import os


def _price_usdc() -> float:
    return float(os.getenv("API_TIER_PRICE_USDC", "299"))


def pricing_tiers() -> list[dict]:
    monthly = _price_usdc()
    d6 = float(os.getenv("API_TIER_DISCOUNT_6MO_PCT", "5"))
    d12 = float(os.getenv("API_TIER_DISCOUNT_12MO_PCT", "10"))
    return [
        {"days": 30,  "months": 1,  "price_usdc": round(monthly, 2)},
        {"days": 180, "months": 6,  "price_usdc": round(monthly * 6 * (1 - d6 / 100), 2)},
        {"days": 365, "months": 12, "price_usdc": round(monthly * 12 * (1 - d12 / 100), 2)},
    ]


def payments_enabled() -> bool:
    return os.getenv("API_TIER_PAYMENTS_ENABLED", "false").lower() == "true"


def receive_address() -> str:
    return os.getenv("API_TIER_RECEIVE_ADDRESS", "")
