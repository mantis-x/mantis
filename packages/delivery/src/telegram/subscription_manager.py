"""
SubscriptionManager (Telegram) — thin int-chat_id wrapper around the
shared, Postgres-backed src.common.subscription_manager.SubscriptionManager
(channel="telegram").

Previously this was its own parallel in-memory implementation, duplicating
almost all of the common one's logic. Telegram's chat_id is the only
channel identifier that's an int rather than a string (LINE userIds and
Discord channel ids are already strings), so this wrapper's only job is
translating chat_id (int) <-> recipient_id (str) at the boundary — every
method below just stringifies its chat_id argument(s) and delegates.
"""
from __future__ import annotations

from typing import Optional

from src.common.subscription_manager import SubscriptionManager as _CommonSubscriptionManager
from src.common.subscription_manager import Subscription as _CommonSubscription

FREE_DAILY_LIMIT = 3


class Subscription(_CommonSubscription):
    """Same shape as the common Subscription, with chat_id (int) instead of recipient_id (str)."""

    def __init__(self, chat_id: int, **kwargs):
        super().__init__(recipient_id=str(chat_id), **kwargs)
        self.chat_id = chat_id


class SubscriptionManager:
    def __init__(self):
        self._store = _CommonSubscriptionManager(channel="telegram")

    def subscribe(self, chat_id: int) -> bool:
        return self._store.subscribe(str(chat_id))

    def set_chains(self, chat_id: int, chains: Optional[set]) -> bool:
        return self._store.set_chains(str(chat_id), chains)

    def unsubscribe(self, chat_id: int) -> bool:
        return self._store.unsubscribe(str(chat_id))

    def is_subscribed(self, chat_id: int) -> bool:
        return self._store.is_subscribed(str(chat_id))

    def get_subscribers(self, signal: dict) -> list[int]:
        """Return chat_ids (int) that should receive this signal."""
        return [int(rid) for rid in self._store.get_subscribers(signal)]

    def record_delivery(self, chat_id: int) -> None:
        self._store.record_delivery(str(chat_id))

    def register_wallet(self, chat_id: int, wallet_address: str) -> bool:
        return self._store.register_wallet(str(chat_id), wallet_address)

    def add_to_history(self, signal: dict) -> None:
        self._store.add_to_history(signal)

    def get_history(self, limit: int = 5) -> list[dict]:
        return self._store.get_history(limit)

    def get_signal_by_id(self, signal_id) -> Optional[dict]:
        return self._store.get_signal_by_id(signal_id)

    def subscriber_count(self) -> int:
        return self._store.subscriber_count()

    def get_subscription(self, chat_id: int) -> Optional[Subscription]:
        common_sub = self._store.get_subscription(str(chat_id))
        if common_sub is None:
            return None
        return Subscription(
            chat_id=chat_id,
            min_confidence=common_sub.min_confidence,
            signal_types=common_sub.signal_types,
            protocols=common_sub.protocols,
            chains=common_sub.chains,
            is_pro=common_sub.is_pro,
            pro_expires_at=common_sub.pro_expires_at,
            registered_wallet=common_sub.registered_wallet,
            joined_at=common_sub.joined_at,
            alerts_today=common_sub.alerts_today,
            last_alert_date=common_sub.last_alert_date,
        )
