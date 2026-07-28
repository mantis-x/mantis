"""
ApiPaymentWatcher — the API tier's ($299/mo) self-serve billing. Structurally
a sibling of ProPaymentWatcher (same read-only, key-free collection model, same
idempotency/confirmation/cursor discipline) with two deliberate differences:

  1. Product distinction is by RECEIVE ADDRESS, not amount. API payments go to
     API_TIER_RECEIVE_ADDRESS (a different wallet than PRO_TIER_RECEIVE_ADDRESS),
     so a $299 transfer can never be confused with a Pro payment — the two
     watchers scan disjoint destination addresses. (docs/api_tier_readiness.md §7.)
  2. It credits an ApiCustomerRow (standalone entity), setting api_tier_expires_at.
     No stored "is_active" flag is needed — auth checks api_tier_expires_at live
     (ApiCustomerRow.is_active()), so an expired customer is denied at request
     time with no sweep required.

Disabled by default (API_TIER_PAYMENTS_ENABLED=false) and never touches the RPC
while disabled. Accepting real money is still the Phase-3 legal/entity decision
the account holder owns — do not set API_TIER_RECEIVE_ADDRESS / flip
API_TIER_PAYMENTS_ENABLED=true until that's settled.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from web3 import Web3

from src.db.models.api_customer import ApiCustomerRow
from src.db.models.api_payment import ApiPaymentRow

log = logging.getLogger(__name__)

API_TIER_PAYMENTS_ENABLED = os.getenv("API_TIER_PAYMENTS_ENABLED", "false").lower() == "true"
API_TIER_RECEIVE_ADDRESS  = os.getenv("API_TIER_RECEIVE_ADDRESS", "")
API_TIER_PRICE_USDC       = float(os.getenv("API_TIER_PRICE_USDC", "299"))   # 1-month price
API_TIER_DISCOUNT_6MO_PCT  = float(os.getenv("API_TIER_DISCOUNT_6MO_PCT", "5"))
API_TIER_DISCOUNT_12MO_PCT = float(os.getenv("API_TIER_DISCOUNT_12MO_PCT", "10"))
API_TIER_CONFIRMATIONS    = int(os.getenv("API_TIER_CONFIRMATIONS", "12"))
API_TIER_MAX_BLOCK_SPAN   = int(os.getenv("API_TIER_MAX_BLOCK_SPAN", "2000"))

_TIER_MATCH_TOLERANCE_USDC = 0.01


def api_pricing_tiers() -> list[tuple[int, float]]:
    """(days, price_usdc) per period, longest first, derived live from env — so
    a price change reprices every tier. Public so the GET /v1/billing endpoint
    (mirrored in the api package) can show the same numbers."""
    monthly = API_TIER_PRICE_USDC
    return [
        (365, round(monthly * 12 * (1 - API_TIER_DISCOUNT_12MO_PCT / 100), 2)),
        (180, round(monthly * 6 * (1 - API_TIER_DISCOUNT_6MO_PCT / 100), 2)),
        (30,  monthly),
    ]


def _match_tier(amount_usdc: float) -> Optional[tuple[int, float]]:
    """Largest tier the payment covers (longest-first, so overpayment credits the
    longer period), or None if it doesn't clear even the 1-month price."""
    for days, price in api_pricing_tiers():
        if amount_usdc >= price - _TIER_MATCH_TOLERANCE_USDC:
            return days, price
    return None


# Native USDC on Arbitrum One (same token the Pro watcher uses).
_ARBITRUM_USDC_ADDRESS = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
_USDC_DECIMALS = 6
_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
_REDIS_LAST_BLOCK_KEY = "mantis:billing:api_arbitrum_last_checked_block"


def _topic_to_address(topic) -> str:
    hex_str = topic.hex() if hasattr(topic, "hex") else str(topic)
    return "0x" + hex_str[-40:].lower()


class ApiPaymentWatcher:
    def __init__(self, redis_client, rpc_url: Optional[str] = None):
        self._redis = redis_client
        self._rpc_url = rpc_url or os.getenv("ARBITRUM_RPC_URL", "https://arb1.arbitrum.io/rpc")
        self._w3: Optional[Web3] = None
        self._pending_cursor: Optional[int] = None

    def _w3_client(self) -> Web3:
        if self._w3 is None:
            self._w3 = Web3(Web3.HTTPProvider(self._rpc_url, request_kwargs={"timeout": 30}))
        return self._w3

    def check_new_payments(self, session) -> int:
        """Poll for confirmed USDC payments into API_TIER_RECEIVE_ADDRESS and
        credit matching customers. No RPC call unless enabled + address set.
        Cursor advances only via commit_cursor() after the caller commits."""
        if not API_TIER_PAYMENTS_ENABLED or not API_TIER_RECEIVE_ADDRESS:
            return 0

        w3 = self._w3_client()
        safe_head = w3.eth.block_number - API_TIER_CONFIRMATIONS
        if safe_head < 0:
            return 0

        last_checked = self._redis.get(_REDIS_LAST_BLOCK_KEY)
        from_block = int(last_checked) + 1 if last_checked else safe_head
        if from_block > safe_head:
            return 0
        to_block = min(safe_head, from_block + API_TIER_MAX_BLOCK_SPAN - 1)

        to_topic = "0x" + API_TIER_RECEIVE_ADDRESS.lower().replace("0x", "").rjust(64, "0")
        logs = w3.eth.get_logs({
            "fromBlock": from_block,
            "toBlock": to_block,
            "address": _ARBITRUM_USDC_ADDRESS,
            "topics": [_TRANSFER_TOPIC, None, to_topic],
        })

        credited = sum(self._process_transfer(session, entry) for entry in logs)
        self._pending_cursor = to_block
        return credited

    def commit_cursor(self) -> None:
        if self._pending_cursor is not None:
            self._redis.set(_REDIS_LAST_BLOCK_KEY, self._pending_cursor)
            self._pending_cursor = None

    def _process_transfer(self, session, entry) -> int:
        tx_hash = entry["transactionHash"].hex()
        log_index = entry["logIndex"]

        existing = session.query(ApiPaymentRow).filter(
            ApiPaymentRow.chain == "arbitrum",
            ApiPaymentRow.tx_hash == tx_hash,
            ApiPaymentRow.log_index == log_index,
        ).one_or_none()
        if existing is not None:
            return 0  # idempotency guard

        from_address = _topic_to_address(entry["topics"][1])
        amount_usdc = int(entry["data"].hex(), 16) / (10 ** _USDC_DECIMALS)

        payment = ApiPaymentRow(
            chain="arbitrum", tx_hash=tx_hash, log_index=log_index,
            from_address=from_address, amount_usdc=amount_usdc,
        )

        tier = _match_tier(amount_usdc)
        if tier is None:
            session.add(payment)
            log.warning("API underpayment ignored: %.2f USDC from %s (need >= %.2f)",
                        amount_usdc, from_address, API_TIER_PRICE_USDC)
            return 0
        tier_days, _price = tier

        customer = session.query(ApiCustomerRow).filter(
            ApiCustomerRow.registered_wallet == from_address
        ).order_by(ApiCustomerRow.id.asc()).first()

        if customer is None:
            session.add(payment)
            log.warning("Unmatched API payment: %.2f USDC from %s — no registered_wallet",
                        amount_usdc, from_address)
            return 0

        now = datetime.now(tz=timezone.utc)
        base = customer.api_tier_expires_at if (
            customer.api_tier_expires_at and customer.api_tier_expires_at > now
        ) else now
        customer.api_tier_expires_at = base + timedelta(days=tier_days)

        payment.matched = True
        payment.credited_customer_id = customer.id
        session.add(payment)

        log.info("API tier credited: customer=%d wallet=%s amount=%.2f days=%d expires=%s",
                 customer.id, from_address, amount_usdc, tier_days, customer.api_tier_expires_at)
        return 1
