"""
ProPaymentWatcher — polls Arbitrum for USDC Transfer events into the Pro-tier
receiving wallet and credits the matching subscriber's is_pro/pro_expires_at.

Disabled by default (PRO_TIER_PAYMENTS_ENABLED=false) — mirrors Execute's
BYREAL_DRY_RUN gate. This mechanism is read-only collection: it only ever
*watches* for incoming transfers and never signs or spends, so no private
key is needed anywhere in this path (PRO_TIER_RECEIVE_ADDRESS should be a
wallet the account holder controls, ideally cold/hardware — the app never
needs its key). Accepting real money is still a Phase-3 legal/entity
decision the account holder owns (see docs/execute_readiness.md §6) — do
not set PRO_TIER_RECEIVE_ADDRESS or flip PRO_TIER_PAYMENTS_ENABLED=true in
production until that's settled.

Payment model: pay-for-a-period, not a recurring on-chain subscription.
Three periods are offered — 1/6/12 months, the longer ones discounted — and
matched by amount (see PRICING_TIERS below): a subscriber runs
/register_wallet <address> once, then sends the USDC amount for whichever
period they want on Arbitrum One from that wallet to PRO_TIER_RECEIVE_ADDRESS.
On a confirmed, credited transfer, is_pro=True and pro_expires_at is extended
by that tier's day count from max(now, current expiry) — paying early stacks
days rather than wasting them.

Idempotency: every transfer's (chain, tx_hash, log_index) is recorded in
pro_payments (unique constraint) before crediting, so a re-poll or worker
restart can never double-credit the same transfer. Underpayments and
payments from an unregistered wallet are recorded as unmatched (for audit
and to stop reprocessing) but never credited automatically.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from web3 import Web3

from src.db.models.pro_payment import ProPaymentRow
from src.db.models.subscription import SubscriptionRow

log = logging.getLogger(__name__)

PRO_TIER_PAYMENTS_ENABLED = os.getenv("PRO_TIER_PAYMENTS_ENABLED", "false").lower() == "true"
PRO_TIER_RECEIVE_ADDRESS  = os.getenv("PRO_TIER_RECEIVE_ADDRESS", "")
PRO_TIER_PRICE_USDC       = float(os.getenv("PRO_TIER_PRICE_USDC", "29"))  # 1-month price; 6/12mo below are derived from this
PRO_TIER_DISCOUNT_6MO_PCT  = float(os.getenv("PRO_TIER_DISCOUNT_6MO_PCT", "5"))
PRO_TIER_DISCOUNT_12MO_PCT = float(os.getenv("PRO_TIER_DISCOUNT_12MO_PCT", "10"))
PRO_TIER_CONFIRMATIONS    = int(os.getenv("PRO_TIER_CONFIRMATIONS", "12"))
# Small absolute tolerance for float/stablecoin-precision comparisons when
# matching a transfer's amount to a tier price — not a discount on the price.
_TIER_MATCH_TOLERANCE_USDC = 0.01


def _pricing_tiers() -> list[tuple[int, float]]:
    """(days, price_usdc) for each period, longest first, computed live from
    PRO_TIER_PRICE_USDC + the discount percents — so an env change takes
    effect without redefining tiers by hand."""
    monthly = PRO_TIER_PRICE_USDC
    return [
        (365, round(monthly * 12 * (1 - PRO_TIER_DISCOUNT_12MO_PCT / 100), 2)),
        (180, round(monthly * 6 * (1 - PRO_TIER_DISCOUNT_6MO_PCT / 100), 2)),
        (30,  monthly),
    ]


def _match_tier(amount_usdc: float) -> Optional[tuple[int, float]]:
    """Largest tier the payment covers, or None if it doesn't clear even the
    cheapest (1-month) tier. Tiers are checked longest-first so overpayment
    (e.g. sending the 12mo amount) is credited at the longer period, not
    silently downgraded to the 1-month match."""
    for days, price in _pricing_tiers():
        if amount_usdc >= price - _TIER_MATCH_TOLERANCE_USDC:
            return days, price
    return None
# Cap on blocks scanned per tick. Public Arbitrum eth_getLogs rate-limits large
# ranges (see .env.example's ARBITRUM_RPC_URL note) — after any worker downtime
# the naive from_block..safe_head span can be huge; without this cap a single
# oversized request fails every tick forever (same from_block resubmitted).
PRO_TIER_MAX_BLOCK_SPAN   = int(os.getenv("PRO_TIER_MAX_BLOCK_SPAN", "2000"))

# Native USDC on Arbitrum One — verified on-chain (token1() of a real Uniswap
# V3 pool queried this session; see packages/ingestion/src/chains.py _ARB_USDC).
_ARBITRUM_USDC_ADDRESS = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
_USDC_DECIMALS = 6

# keccak256("Transfer(address,address,uint256)") — standard ERC-20 Transfer topic0.
_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

_REDIS_LAST_BLOCK_KEY = "mantis:billing:arbitrum_last_checked_block"


def _topic_to_address(topic) -> str:
    """A Transfer event's indexed address topic is a 32-byte left-padded word."""
    hex_str = topic.hex() if hasattr(topic, "hex") else str(topic)
    return "0x" + hex_str[-40:].lower()


class ProPaymentWatcher:
    """Polls Arbitrum for USDC Transfer events into PRO_TIER_RECEIVE_ADDRESS."""

    def __init__(self, redis_client, rpc_url: Optional[str] = None):
        self._redis = redis_client
        self._rpc_url = rpc_url or os.getenv("ARBITRUM_RPC_URL", "https://arb1.arbitrum.io/rpc")
        self._w3: Optional[Web3] = None  # lazily connected — no RPC call at all while disabled
        self._pending_cursor: Optional[int] = None

    def _w3_client(self) -> Web3:
        if self._w3 is None:
            self._w3 = Web3(Web3.HTTPProvider(self._rpc_url, request_kwargs={"timeout": 30}))
        return self._w3

    def check_new_payments(self, session) -> int:
        """Poll for newly-confirmed USDC payments and credit matching subscribers.
        No RPC call at all unless both PRO_TIER_PAYMENTS_ENABLED and
        PRO_TIER_RECEIVE_ADDRESS are set. Returns the number of transfers credited.

        The scanned-through-block cursor is only advanced in Redis after this
        call returns and the caller's session has committed (see
        `commit_cursor`) — so a DB commit failure never leaves a scanned
        block range unrecorded (which would silently skip payments in it)."""
        if not PRO_TIER_PAYMENTS_ENABLED or not PRO_TIER_RECEIVE_ADDRESS:
            return 0

        w3 = self._w3_client()
        latest = w3.eth.block_number
        safe_head = latest - PRO_TIER_CONFIRMATIONS
        if safe_head < 0:
            return 0

        last_checked = self._redis.get(_REDIS_LAST_BLOCK_KEY)
        from_block = int(last_checked) + 1 if last_checked else safe_head
        if from_block > safe_head:
            return 0

        # Chunk the scan so a long worker outage can't produce one oversized
        # eth_getLogs call that fails forever against a rate-limited public RPC.
        to_block = min(safe_head, from_block + PRO_TIER_MAX_BLOCK_SPAN - 1)

        to_topic = "0x" + PRO_TIER_RECEIVE_ADDRESS.lower().replace("0x", "").rjust(64, "0")

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
        """Advance the Redis scan cursor — call only after the session that
        ran check_new_payments has successfully committed."""
        if self._pending_cursor is not None:
            self._redis.set(_REDIS_LAST_BLOCK_KEY, self._pending_cursor)
            self._pending_cursor = None

    def _process_transfer(self, session, entry) -> int:
        tx_hash = entry["transactionHash"].hex()
        log_index = entry["logIndex"]

        existing = session.query(ProPaymentRow).filter(
            ProPaymentRow.chain == "arbitrum",
            ProPaymentRow.tx_hash == tx_hash,
            ProPaymentRow.log_index == log_index,
        ).one_or_none()
        if existing is not None:
            return 0  # already processed — idempotency guard

        from_address = _topic_to_address(entry["topics"][1])
        amount_usdc = int(entry["data"].hex(), 16) / (10 ** _USDC_DECIMALS)

        payment = ProPaymentRow(
            chain="arbitrum",
            tx_hash=tx_hash,
            log_index=log_index,
            from_address=from_address,
            amount_usdc=amount_usdc,
        )

        tier = _match_tier(amount_usdc)
        if tier is None:
            session.add(payment)
            log.warning(
                "Underpayment ignored: %.2f USDC from %s (need at least %.2f for 1 month)",
                amount_usdc, from_address, PRO_TIER_PRICE_USDC,
            )
            return 0
        tier_days, _tier_price = tier

        # .first(), not .one_or_none(): registered_wallet has no unique
        # constraint (two subscribers could register the same address), and
        # a MultipleResultsFound here would propagate up and stall both
        # crediting and the expiry sweep on every future tick.
        row = session.query(SubscriptionRow).filter(
            SubscriptionRow.registered_wallet == from_address
        ).order_by(SubscriptionRow.id.asc()).first()

        if row is None:
            session.add(payment)
            log.warning(
                "Unmatched payment: %.2f USDC from %s — no registered_wallet found",
                amount_usdc, from_address,
            )
            return 0

        now = datetime.now(tz=timezone.utc)
        base = row.pro_expires_at if (row.pro_expires_at and row.pro_expires_at > now) else now
        row.pro_expires_at = base + timedelta(days=tier_days)
        row.is_pro = True

        payment.matched = True
        payment.credited_channel = row.channel
        payment.credited_recipient_id = row.recipient_id
        session.add(payment)

        log.info(
            "Pro tier credited: channel=%s id=%s wallet=%s amount=%.2f days=%d expires=%s",
            row.channel, row.recipient_id, from_address, amount_usdc, tier_days, row.pro_expires_at,
        )
        return 1


def sweep_expired_pro(session) -> int:
    """Revert is_pro to False for subscribers whose pro_expires_at has
    lapsed. NULL pro_expires_at (manual/grandfathered grants) is treated as
    'never expires' and is never touched by this sweep."""
    now = datetime.now(tz=timezone.utc)
    rows = session.query(SubscriptionRow).filter(
        SubscriptionRow.is_pro.is_(True),
        SubscriptionRow.pro_expires_at.isnot(None),
        SubscriptionRow.pro_expires_at < now,
    ).all()
    for row in rows:
        row.is_pro = False
        log.info("Pro tier expired: channel=%s id=%s", row.channel, row.recipient_id)
    return len(rows)
