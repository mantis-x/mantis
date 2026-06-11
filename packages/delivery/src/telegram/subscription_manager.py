"""
SubscriptionManager — manages user subscriptions in memory.

Stores: {chat_id: {min_confidence, signal_types, protocols}}
Production: replace with Postgres subscriptions table.

Free tier: 3 alerts/day cap (tracked per chat_id).
Pro tier: unlimited (set by manually toggling is_pro).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, date, timezone
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)

FREE_DAILY_LIMIT = 3


@dataclass
class Subscription:
    chat_id:        int
    min_confidence: int             = 60
    signal_types:   Optional[set]   = None   # None = all types
    protocols:      Optional[set]   = None   # None = all protocols
    is_pro:         bool            = False
    joined_at:      datetime        = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
    # Daily alert tracking
    alerts_today:   int             = 0
    last_alert_date: Optional[date] = None

    def can_receive(self, signal: dict) -> bool:
        """Check if this subscriber should receive this signal."""
        # Confidence filter
        if signal.get("confidence", 0) < self.min_confidence:
            return False
        # Signal type filter
        if self.signal_types and signal.get("signal_type") not in self.signal_types:
            return False
        # Protocol filter
        if self.protocols and signal.get("protocol") not in self.protocols:
            return False
        # Daily cap (free tier)
        if not self.is_pro:
            today = datetime.now(tz=timezone.utc).date()
            if self.last_alert_date != today:
                self.alerts_today   = 0
                self.last_alert_date = today
            if self.alerts_today >= FREE_DAILY_LIMIT:
                return False
        return True

    def record_alert(self) -> None:
        """Increment daily alert counter."""
        today = datetime.now(tz=timezone.utc).date()
        if self.last_alert_date != today:
            self.alerts_today   = 0
            self.last_alert_date = today
        self.alerts_today += 1


class SubscriptionManager:
    def __init__(self):
        self._subs: dict[int, Subscription] = {}
        self._signal_history: list[dict]    = []   # last 50 signals

    def subscribe(self, chat_id: int) -> bool:
        """Subscribe a chat. Returns True if new, False if already subscribed."""
        if chat_id in self._subs:
            return False
        self._subs[chat_id] = Subscription(chat_id=chat_id)
        log.info("New subscriber: chat_id=%d total=%d", chat_id, len(self._subs))
        return True

    def unsubscribe(self, chat_id: int) -> bool:
        """Unsubscribe. Returns True if was subscribed."""
        if chat_id not in self._subs:
            return False
        del self._subs[chat_id]
        log.info("Unsubscribed: chat_id=%d remaining=%d", chat_id, len(self._subs))
        return True

    def is_subscribed(self, chat_id: int) -> bool:
        return chat_id in self._subs

    def get_subscribers(self, signal: dict) -> list[int]:
        """Return chat_ids that should receive this signal."""
        eligible = []
        for chat_id, sub in self._subs.items():
            if sub.can_receive(signal):
                eligible.append(chat_id)
        return eligible

    def record_delivery(self, chat_id: int) -> None:
        if chat_id in self._subs:
            self._subs[chat_id].record_alert()

    def add_to_history(self, signal: dict) -> None:
        self._signal_history.insert(0, signal)
        self._signal_history = self._signal_history[:50]

    def get_history(self, limit: int = 5) -> list[dict]:
        return self._signal_history[:limit]

    def subscriber_count(self) -> int:
        return len(self._subs)

    def get_subscription(self, chat_id: int) -> Optional[Subscription]:
        return self._subs.get(chat_id)
