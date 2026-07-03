"""
SubscriptionManager — generic in-memory subscription store, shared by
the LINE and Discord bots (Telegram keeps its own copy at
src/telegram/subscription_manager.py).

Stores: {recipient_id: {min_confidence, signal_types, protocols}}
Production: replace with Postgres subscriptions table.

Free tier: 3 alerts/day cap (tracked per recipient_id).
Pro tier: unlimited (set by manually toggling is_pro).

recipient_id is a str: a LINE userId or a Discord channel id.
"""
from __future__ import annotations

import logging
from datetime import datetime, date, timezone
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)

FREE_DAILY_LIMIT = 3


@dataclass
class Subscription:
    recipient_id:   str
    min_confidence: int             = 60
    signal_types:   Optional[set]   = None   # None = all types
    protocols:      Optional[set]   = None   # None = all protocols
    chains:         Optional[set]   = None   # None = all chains (mantle, arbitrum, …)
    is_pro:         bool            = False
    joined_at:      datetime        = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
    # Daily alert tracking
    alerts_today:    int             = 0
    last_alert_date: Optional[date]  = None

    def can_receive(self, signal: dict) -> bool:
        """Check if this subscriber should receive this signal."""
        if signal.get("confidence", 0) < self.min_confidence:
            return False
        if self.signal_types and signal.get("signal_type") not in self.signal_types:
            return False
        if self.protocols and signal.get("protocol") not in self.protocols:
            return False
        if self.chains and signal.get("chain", "mantle") not in self.chains:
            return False
        if not self.is_pro:
            today = datetime.now(tz=timezone.utc).date()
            if self.last_alert_date != today:
                self.alerts_today    = 0
                self.last_alert_date = today
            if self.alerts_today >= FREE_DAILY_LIMIT:
                return False
        return True

    def record_alert(self) -> None:
        """Increment daily alert counter."""
        today = datetime.now(tz=timezone.utc).date()
        if self.last_alert_date != today:
            self.alerts_today    = 0
            self.last_alert_date = today
        self.alerts_today += 1


class SubscriptionManager:
    def __init__(self):
        self._subs: dict[str, Subscription] = {}
        self._signal_history: list[dict]    = []   # last 50 signals

    def subscribe(self, recipient_id: str) -> bool:
        """Subscribe a recipient. Returns True if new, False if already subscribed."""
        if recipient_id in self._subs:
            return False
        self._subs[recipient_id] = Subscription(recipient_id=recipient_id)
        log.info("New subscriber: id=%s total=%d", recipient_id, len(self._subs))
        return True

    def set_chains(self, recipient_id: str, chains: Optional[set]) -> bool:
        """Set chain filter for a subscriber. None = all chains. Returns False if not subscribed."""
        sub = self._subs.get(recipient_id)
        if sub is None:
            return False
        sub.chains = chains
        log.info("Chain filter set: id=%s chains=%s", recipient_id, chains)
        return True

    def unsubscribe(self, recipient_id: str) -> bool:
        """Unsubscribe. Returns True if was subscribed."""
        if recipient_id not in self._subs:
            return False
        del self._subs[recipient_id]
        log.info("Unsubscribed: id=%s remaining=%d", recipient_id, len(self._subs))
        return True

    def is_subscribed(self, recipient_id: str) -> bool:
        return recipient_id in self._subs

    def get_subscribers(self, signal: dict) -> list[str]:
        """Return recipient_ids that should receive this signal."""
        return [rid for rid, sub in self._subs.items() if sub.can_receive(signal)]

    def record_delivery(self, recipient_id: str) -> None:
        if recipient_id in self._subs:
            self._subs[recipient_id].record_alert()

    def add_to_history(self, signal: dict) -> None:
        self._signal_history.insert(0, signal)
        self._signal_history = self._signal_history[:50]

    def get_history(self, limit: int = 5) -> list[dict]:
        return self._signal_history[:limit]

    def subscriber_count(self) -> int:
        return len(self._subs)

    def get_subscription(self, recipient_id: str) -> Optional[Subscription]:
        return self._subs.get(recipient_id)
