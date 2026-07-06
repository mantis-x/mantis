"""
Integration tests for the Postgres-backed SubscriptionManager (common
store, shared by Discord/LINE, and the Telegram int-chat_id wrapper).

Requires a disposable Postgres reachable at TEST_DATABASE_URL (defaults to
localhost:5433 — deliberately not 5432, the docker-compose default, since
this file's fixtures create/drop tables).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

_TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql://mantis:password@localhost:5433/mantis"
)
if ":5432/" in _TEST_DATABASE_URL and "ALLOW_TEST_DB_ON_5432" not in os.environ:
    raise RuntimeError(
        "Refusing to run: TEST_DATABASE_URL points at port 5432. This file's "
        "fixtures create/drop tables — point it at a disposable instance instead."
    )
os.environ.setdefault("DATABASE_URL", _TEST_DATABASE_URL)

from src.db.connection import get_engine, get_session, reset_engine_for_tests
from src.db.models import Base
from src.db.models.subscription import SubscriptionRow
from src.common.subscription_manager import SubscriptionManager
from src.telegram.subscription_manager import SubscriptionManager as TelegramSubscriptionManager


@pytest.fixture(scope="module", autouse=True)
def _db_schema():
    reset_engine_for_tests()
    engine = get_engine()
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean_subscriptions():
    with get_session() as session:
        session.query(SubscriptionRow).delete()
    yield


def _signal(**overrides):
    base = {"confidence": 80, "signal_type": "accumulation", "protocol": "gmx", "chain": "arbitrum"}
    base.update(overrides)
    return base


class TestSubscribeUnsubscribe:
    def test_subscribe_returns_true_for_new_recipient(self):
        mgr = SubscriptionManager(channel="discord")
        assert mgr.subscribe("chan1") is True

    def test_subscribe_returns_false_if_already_subscribed(self):
        mgr = SubscriptionManager(channel="discord")
        mgr.subscribe("chan1")
        assert mgr.subscribe("chan1") is False

    def test_unsubscribe_returns_true_if_was_subscribed(self):
        mgr = SubscriptionManager(channel="discord")
        mgr.subscribe("chan1")
        assert mgr.unsubscribe("chan1") is True

    def test_unsubscribe_returns_false_if_not_subscribed(self):
        mgr = SubscriptionManager(channel="discord")
        assert mgr.unsubscribe("never-subscribed") is False

    def test_is_subscribed_reflects_state(self):
        mgr = SubscriptionManager(channel="line")
        assert mgr.is_subscribed("user1") is False
        mgr.subscribe("user1")
        assert mgr.is_subscribed("user1") is True


class TestChannelIsolation:
    """The same recipient_id string on different channels must not collide."""

    def test_same_id_different_channels_are_independent(self):
        discord_mgr = SubscriptionManager(channel="discord")
        line_mgr    = SubscriptionManager(channel="line")

        discord_mgr.subscribe("shared-id-123")
        assert discord_mgr.is_subscribed("shared-id-123") is True
        assert line_mgr.is_subscribed("shared-id-123") is False

    def test_subscriber_count_scoped_per_channel(self):
        discord_mgr = SubscriptionManager(channel="discord")
        line_mgr    = SubscriptionManager(channel="line")

        discord_mgr.subscribe("a")
        discord_mgr.subscribe("b")
        line_mgr.subscribe("c")

        assert discord_mgr.subscriber_count() == 2
        assert line_mgr.subscriber_count() == 1


class TestGetSubscribers:
    def test_filters_by_confidence(self):
        mgr = SubscriptionManager(channel="discord")
        mgr.subscribe("recipient1")
        with get_session() as session:
            row = session.query(SubscriptionRow).filter(
                SubscriptionRow.recipient_id == "recipient1"
            ).one()
            row.min_confidence = 90

        recipients = mgr.get_subscribers(_signal(confidence=80))
        assert "recipient1" not in recipients

    def test_free_tier_daily_cap(self):
        mgr = SubscriptionManager(channel="discord")
        mgr.subscribe("capped")
        for _ in range(3):
            mgr.record_delivery("capped")

        recipients = mgr.get_subscribers(_signal())
        assert "capped" not in recipients

    def test_pro_tier_bypasses_daily_cap(self):
        mgr = SubscriptionManager(channel="discord")
        mgr.subscribe("pro-user")
        with get_session() as session:
            row = session.query(SubscriptionRow).filter(
                SubscriptionRow.recipient_id == "pro-user"
            ).one()
            row.is_pro = True
        for _ in range(10):
            mgr.record_delivery("pro-user")

        recipients = mgr.get_subscribers(_signal())
        assert "pro-user" in recipients

    def test_chain_filter(self):
        mgr = SubscriptionManager(channel="discord")
        mgr.subscribe("mantle-only")
        mgr.set_chains("mantle-only", {"mantle"})

        recipients = mgr.get_subscribers(_signal(chain="arbitrum"))
        assert "mantle-only" not in recipients

        recipients = mgr.get_subscribers(_signal(chain="mantle"))
        assert "mantle-only" in recipients


class TestPersistenceAcrossRestarts:
    def test_subscribers_survive_new_manager_instance(self):
        """The whole point: subscriber state must survive a process restart."""
        SubscriptionManager(channel="discord").subscribe("durable-user")
        fresh_mgr = SubscriptionManager(channel="discord")
        assert fresh_mgr.is_subscribed("durable-user") is True


class TestTelegramWrapper:
    """The int-chat_id wrapper must translate correctly and share the same store."""

    def test_subscribe_accepts_int_chat_id(self):
        mgr = TelegramSubscriptionManager()
        assert mgr.subscribe(12345) is True

    def test_is_subscribed_with_int(self):
        mgr = TelegramSubscriptionManager()
        mgr.subscribe(12345)
        assert mgr.is_subscribed(12345) is True

    def test_get_subscribers_returns_ints(self):
        mgr = TelegramSubscriptionManager()
        mgr.subscribe(999)
        recipients = mgr.get_subscribers(_signal())
        assert 999 in recipients
        assert all(isinstance(r, int) for r in recipients)

    def test_telegram_and_common_store_share_the_channel(self):
        """Telegram's wrapper must persist through the same 'telegram' channel row."""
        TelegramSubscriptionManager().subscribe(555)
        common_view = SubscriptionManager(channel="telegram")
        assert common_view.is_subscribed("555") is True
