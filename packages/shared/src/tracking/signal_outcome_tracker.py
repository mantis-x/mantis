"""
SignalOutcomeTracker — the core of Mantis's track record.

For every signal, this:
  1. Persists the signal itself (the first durable copy anywhere — Redis's
     mantis:signals is a capped, ephemeral display log; nothing today
     writes signals to a store you could analyze weeks later).
  2. Resolves which asset the signal is actually about (pool_registry) and
     snapshots its current USD price as the entry price.
  3. Schedules a price re-check at each fixed horizon (1h / 4h / 24h / 7d).
  4. On a periodic tick, finds due re-checks, fetches the current price,
     and records the % move — this is what a backtest reads.

Signal types imply a direction: ACCUMULATION / WHALE_ENTRY predict the
asset moves up; DISTRIBUTION / WHALE_EXIT predict it moves down;
UNUSUAL_VOLUME makes no directional claim (excluded from hit-rate scoring,
included in the raw price-move stats).

This is also where a wallet's own track record gets built: when a
directional outcome resolves at the canonical horizon (see
WALLET_TRACK_RECORD_HORIZON), every wallet in that signal's cluster gets a
hit/total counter bumped in Redis. packages/enrichment/src/wallet_track_record.py
reads those counters back out to label "smart money" wallets from Mantis's
own history instead of a paid third-party API.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from src.db.models.signal import SignalRow
from src.db.models.signal_outcome import SignalOutcomeRow, OutcomeStatus, HORIZONS_HOURS
from src.pricing.pool_registry import resolve_price_key
from src.pricing.price_reader import LivePriceReader

log = logging.getLogger(__name__)

# Signal types whose implied direction can be scored against a later price move.
# "up" means a price increase counts as a hit; "down" means a decrease does.
# unusual_volume has no directional claim and is intentionally absent.
DIRECTIONAL_SIGNAL_TYPES = {
    "accumulation": "up",
    "whale_entry":  "up",
    "distribution": "down",
    "whale_exit":   "down",
}

# One horizon per signal is used to update wallet track records — using all
# four would count the same event up to 4x and overweight frequent wallets.
# 24h is long enough for a directional call to have actually played out.
WALLET_TRACK_RECORD_HORIZON = "24h"
WALLET_HITS_KEY  = "mantis:wallet_track_record:hits"
WALLET_TOTAL_KEY = "mantis:wallet_track_record:total"


def is_hit(signal_type: str, pct_change: float | None) -> bool | None:
    """None if signal_type has no directional claim (e.g. unusual_volume/liquidity_added)."""
    direction = DIRECTIONAL_SIGNAL_TYPES.get(signal_type)
    if direction is None or pct_change is None:
        return None
    return pct_change > 0 if direction == "up" else pct_change < 0


class SignalOutcomeTracker:
    def __init__(self, price_reader: LivePriceReader | None = None, redis_client=None):
        self._price_reader = price_reader or LivePriceReader()
        # Sync Redis client (not the async one the tracking worker uses for its
        # queue) — bumping two counters is a cheap blocking call, same pattern
        # as every other synchronous-call-inside-an-async-loop in this codebase.
        self._redis = redis_client

    def persist_signal(self, session: Session, signal_dict: dict) -> SignalRow:
        """Insert one signal (from the mantis:signals payload shape) as a durable row."""
        row = SignalRow(
            chain            = signal_dict["chain"],
            protocol         = signal_dict["protocol"],
            pool_address     = signal_dict["pool_address"],
            wallets          = signal_dict.get("wallets", []),
            signal_type      = signal_dict["signal_type"],
            confidence       = signal_dict["confidence"],
            summary          = signal_dict.get("summary", ""),
            key_factors      = signal_dict.get("key_factors", []),
            detected_at      = _parse_dt(signal_dict["detected_at"]),
            deliver_at       = _parse_dt(signal_dict["deliver_at"]),
            z_score          = signal_dict["z_score"],
            total_volume_usd = signal_dict["total_volume_usd"],
            event_type       = signal_dict.get("event_type", "swap"),
            audit_tx_hash    = signal_dict.get("audit_tx_hash"),
        )
        session.add(row)
        session.flush()  # populate row.id without committing yet
        return row

    def schedule_outcomes(self, session: Session, signal_row: SignalRow) -> list[SignalOutcomeRow]:
        """
        Resolve the tracked asset, snapshot its current price as the entry
        price, and create one pending SignalOutcomeRow per horizon.
        """
        price_key = resolve_price_key(signal_row.chain, signal_row.pool_address)
        entry_price = self._price_reader.get_price(signal_row.chain, price_key)

        outcomes = []
        for label, hours in HORIZONS_HOURS.items():
            outcome = SignalOutcomeRow(
                signal_id       = signal_row.id,
                horizon_label   = label,
                due_at          = signal_row.detected_at + timedelta(hours=hours),
                price_key       = price_key,
                entry_price_usd = entry_price,
                status          = OutcomeStatus.PENDING,
            )
            session.add(outcome)
            outcomes.append(outcome)
        return outcomes

    def check_due_outcomes(self, session: Session, now: datetime | None = None) -> int:
        """
        Find every pending outcome whose due_at has passed, fetch the
        current price, and record the % move. Returns the count processed.
        A feed failure marks that single outcome FAILED (not the whole
        batch) so one bad RPC call doesn't stall every other pending check.
        """
        now = now or datetime.now(tz=timezone.utc)
        due = (
            session.query(SignalOutcomeRow, SignalRow)
            .join(SignalRow, SignalOutcomeRow.signal_id == SignalRow.id)
            .filter(SignalOutcomeRow.status == OutcomeStatus.PENDING)
            .filter(SignalOutcomeRow.due_at <= now)
            .all()
        )

        processed = 0
        for outcome, signal in due:
            try:
                price_now = self._price_reader.get_price(signal.chain, outcome.price_key)
                if outcome.entry_price_usd:
                    outcome.pct_change = round(
                        (price_now - outcome.entry_price_usd) / outcome.entry_price_usd * 100, 4
                    )
                else:
                    outcome.pct_change = None
                outcome.price_usd  = price_now
                outcome.status     = OutcomeStatus.COMPLETED
                outcome.checked_at = now

                if outcome.horizon_label == WALLET_TRACK_RECORD_HORIZON:
                    self._record_wallet_outcomes(signal, outcome.pct_change)
            except Exception as exc:
                log.warning(
                    "check_due_outcomes: price lookup failed for outcome id=%s (%s) — marking failed",
                    outcome.id, exc,
                )
                outcome.status     = OutcomeStatus.FAILED
                outcome.checked_at = now
            processed += 1

        return processed

    def _record_wallet_outcomes(self, signal: SignalRow, pct_change: float | None) -> None:
        """Bump each wallet's hit/total counters for this signal's directional call."""
        if self._redis is None or not signal.wallets:
            return
        hit = is_hit(signal.signal_type, pct_change)
        if hit is None:
            return  # non-directional signal type (unusual_volume, liquidity_added/removed)

        try:
            pipe = self._redis.pipeline()
            for wallet in signal.wallets:
                key = wallet.lower()
                pipe.hincrby(WALLET_TOTAL_KEY, key, 1)
                if hit:
                    pipe.hincrby(WALLET_HITS_KEY, key, 1)
            pipe.execute()
        except Exception as exc:
            log.warning("Wallet track-record Redis update failed: %s", exc)


def _parse_dt(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)
