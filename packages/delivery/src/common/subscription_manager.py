"""
SubscriptionManager — Postgres-backed subscription store, shared by all
three delivery channels (Telegram, Discord, LINE), discriminated by a
`channel` constructor argument.

Previously: three near-identical in-memory implementations (this file, plus
a parallel copy at src/telegram/subscription_manager.py) that reset to zero
subscribers on every Railway redeploy. Now backed by the subscriptions
table via src/db/connection.py — a local mirror of packages/shared/src/db
(see that file's docstring for why it's a copy, not a shared import).

recipient_id is stored as text for every channel. Telegram's integer
chat_id is stringified at its own thin wrapper (src/telegram/subscription_manager.py)
so this class's public contract stays str-based for all three channels.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

from src.db.connection import get_session
from src.db.models.subscription import SubscriptionRow, FREE_DAILY_LIMIT


@dataclass
class Subscription:
    """Detached snapshot of a subscriptions-table row — safe after the session closes."""
    recipient_id:   str
    min_confidence: int = 60
    signal_types:   Optional[set] = None
    protocols:      Optional[set] = None
    chains:         Optional[set] = None
    is_pro:         bool = False
    joined_at:      datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    alerts_today:    int = 0
    last_alert_date: Optional[date] = None

    @classmethod
    def _from_row(cls, row: SubscriptionRow) -> "Subscription":
        return cls(
            recipient_id    = row.recipient_id,
            min_confidence  = row.min_confidence,
            signal_types    = set(row.signal_types) if row.signal_types else None,
            protocols       = set(row.protocols) if row.protocols else None,
            chains          = set(row.chains) if row.chains else None,
            is_pro          = row.is_pro,
            joined_at       = row.joined_at,
            alerts_today    = row.alerts_today,
            last_alert_date = row.last_alert_date,
        )


class SubscriptionManager:
    """Postgres-backed subscriber store for one delivery channel."""

    def __init__(self, channel: str):
        self.channel = channel
        self._signal_history: list[dict] = []  # last 50 signals — display cache only, not persisted

    def subscribe(self, recipient_id: str) -> bool:
        """Subscribe a recipient. Returns True if new, False if already subscribed."""
        with get_session() as session:
            existing = self._get_row(session, recipient_id)
            if existing is not None:
                return False
            session.add(SubscriptionRow(channel=self.channel, recipient_id=str(recipient_id)))
            log.info("New subscriber: channel=%s id=%s", self.channel, recipient_id)
            return True

    def set_chains(self, recipient_id: str, chains: Optional[set]) -> bool:
        """Set chain filter for a subscriber. None = all chains. Returns False if not subscribed."""
        with get_session() as session:
            row = self._get_row(session, recipient_id)
            if row is None:
                return False
            row.chains = list(chains) if chains else None
            log.info("Chain filter set: channel=%s id=%s chains=%s", self.channel, recipient_id, chains)
            return True

    def unsubscribe(self, recipient_id: str) -> bool:
        """Unsubscribe. Returns True if was subscribed."""
        with get_session() as session:
            row = self._get_row(session, recipient_id)
            if row is None:
                return False
            session.delete(row)
            log.info("Unsubscribed: channel=%s id=%s", self.channel, recipient_id)
            return True

    def is_subscribed(self, recipient_id: str) -> bool:
        with get_session() as session:
            return self._get_row(session, recipient_id) is not None

    def get_subscribers(self, signal: dict) -> list[str]:
        """Return recipient_ids that should receive this signal."""
        with get_session() as session:
            rows = session.query(SubscriptionRow).filter(
                SubscriptionRow.channel == self.channel
            ).all()
            return [row.recipient_id for row in rows if row.can_receive(signal)]

    def record_delivery(self, recipient_id: str) -> None:
        with get_session() as session:
            row = self._get_row(session, recipient_id)
            if row is not None:
                row.record_alert()

    def add_to_history(self, signal: dict) -> None:
        self._signal_history.insert(0, signal)
        self._signal_history = self._signal_history[:50]

    def get_history(self, limit: int = 5) -> list[dict]:
        return self._signal_history[:limit]

    def get_signal_by_id(self, signal_id) -> Optional[dict]:
        """Look up a recently-dispatched signal by id, to resolve which
        chain it was logged on (e.g. for /verify). Only searches in-memory
        history (last 50, this process) — older or cross-process signal ids
        return None rather than guessing a chain."""
        try:
            signal_id = int(signal_id)
        except (TypeError, ValueError):
            return None
        for signal in self._signal_history:
            if signal.get("id") == signal_id:
                return signal
        return None

    def subscriber_count(self) -> int:
        with get_session() as session:
            return session.query(SubscriptionRow).filter(
                SubscriptionRow.channel == self.channel
            ).count()

    def get_subscription(self, recipient_id: str) -> Optional[Subscription]:
        with get_session() as session:
            row = self._get_row(session, recipient_id)
            return Subscription._from_row(row) if row else None

    def _get_row(self, session, recipient_id: str) -> Optional[SubscriptionRow]:
        return session.query(SubscriptionRow).filter(
            SubscriptionRow.channel == self.channel,
            SubscriptionRow.recipient_id == str(recipient_id),
        ).one_or_none()
