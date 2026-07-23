"""
Tests for ProPaymentWatcher / sweep_expired_pro against a real Postgres
(see conftest.py) and fakeredis. The Arbitrum RPC (web3.Web3) is mocked —
these tests exercise the crediting/idempotency/matching logic, not live
chain connectivity.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import fakeredis
import pytest

import src.billing.pro_payment_watcher as watcher_mod
from src.billing.pro_payment_watcher import ProPaymentWatcher, sweep_expired_pro
from src.db.models.subscription import SubscriptionRow
from src.db.models.pro_payment import ProPaymentRow

_RECEIVE = "0x1111111111111111111111111111111111111111"
_PAYER   = "0x2222222222222222222222222222222222222222"


def _transfer_log(*, from_addr=_PAYER, amount_usdc=29.0, tx_hash="0xaaa", log_index=0):
    to_topic = MagicMock()
    to_topic.hex.return_value = "0x" + "0" * 24 + _RECEIVE[2:]
    from_topic = MagicMock()
    from_topic.hex.return_value = "0x" + "0" * 24 + from_addr[2:]
    tx = MagicMock()
    tx.hex.return_value = tx_hash
    data = MagicMock()
    data.hex.return_value = hex(int(amount_usdc * 10**6))
    return {
        "transactionHash": tx,
        "logIndex": log_index,
        "topics": [MagicMock(), from_topic, to_topic],
        "data": data,
    }


@pytest.fixture(autouse=True)
def _enable_and_configure(monkeypatch):
    monkeypatch.setattr(watcher_mod, "PRO_TIER_PAYMENTS_ENABLED", True)
    monkeypatch.setattr(watcher_mod, "PRO_TIER_RECEIVE_ADDRESS", _RECEIVE)
    monkeypatch.setattr(watcher_mod, "PRO_TIER_PRICE_USDC", 29.0)
    monkeypatch.setattr(watcher_mod, "PRO_TIER_DISCOUNT_6MO_PCT", 5.0)
    monkeypatch.setattr(watcher_mod, "PRO_TIER_DISCOUNT_12MO_PCT", 10.0)
    monkeypatch.setattr(watcher_mod, "PRO_TIER_CONFIRMATIONS", 0)


def _make_watcher(logs):
    r = fakeredis.FakeStrictRedis(decode_responses=True)
    w = ProPaymentWatcher(redis_client=r)
    fake_w3 = MagicMock()
    fake_w3.eth.block_number = 1000
    fake_w3.eth.get_logs.return_value = logs
    w._w3 = fake_w3
    return w


class TestDisabledByDefault:
    def test_disabled_flag_makes_no_rpc_call(self, db_session, monkeypatch):
        monkeypatch.setattr(watcher_mod, "PRO_TIER_PAYMENTS_ENABLED", False)
        r = fakeredis.FakeStrictRedis(decode_responses=True)
        w = ProPaymentWatcher(redis_client=r)
        w._w3 = MagicMock()
        assert w.check_new_payments(db_session) == 0
        w._w3.eth.get_logs.assert_not_called()

    def test_no_receive_address_makes_no_rpc_call(self, db_session, monkeypatch):
        monkeypatch.setattr(watcher_mod, "PRO_TIER_RECEIVE_ADDRESS", "")
        r = fakeredis.FakeStrictRedis(decode_responses=True)
        w = ProPaymentWatcher(redis_client=r)
        w._w3 = MagicMock()
        assert w.check_new_payments(db_session) == 0
        w._w3.eth.get_logs.assert_not_called()


class TestCrediting:
    def test_matched_payment_credits_pro_and_sets_expiry(self, db_session):
        row = SubscriptionRow(channel="telegram", recipient_id="42", registered_wallet=_PAYER)
        db_session.add(row)
        db_session.flush()

        w = _make_watcher([_transfer_log()])
        credited = w.check_new_payments(db_session)
        db_session.flush()

        assert credited == 1
        assert row.is_pro is True
        assert row.pro_expires_at is not None
        assert row.pro_expires_at > datetime.now(tz=timezone.utc) + timedelta(days=29)

        payment = db_session.query(ProPaymentRow).one()
        assert payment.matched is True
        assert payment.credited_recipient_id == "42"

    def test_repolling_same_transfer_does_not_double_credit(self, db_session):
        row = SubscriptionRow(channel="telegram", recipient_id="42", registered_wallet=_PAYER)
        db_session.add(row)
        db_session.flush()

        log_entry = _transfer_log()
        w1 = _make_watcher([log_entry])
        w1.check_new_payments(db_session)
        db_session.flush()
        expires_after_first = row.pro_expires_at

        # Same watcher instance and same underlying redis state: last_checked_block
        # advances, so a genuinely fresh poll wouldn't re-fetch this log. Simulate
        # the idempotency guard directly: re-processing the *same* log entry
        # (e.g. a restart replaying from an earlier block) must be a no-op.
        credited_again = w1._process_transfer(db_session, log_entry)
        db_session.flush()

        assert credited_again == 0
        assert row.pro_expires_at == expires_after_first
        assert db_session.query(ProPaymentRow).count() == 1

    def test_extends_from_existing_future_expiry_not_from_now(self, db_session):
        future_expiry = datetime.now(tz=timezone.utc) + timedelta(days=10)
        row = SubscriptionRow(
            channel="telegram", recipient_id="42", registered_wallet=_PAYER,
            is_pro=True, pro_expires_at=future_expiry,
        )
        db_session.add(row)
        db_session.flush()

        w = _make_watcher([_transfer_log()])
        w.check_new_payments(db_session)
        db_session.flush()

        # Should stack on top of the existing future expiry, not reset from now.
        assert row.pro_expires_at > future_expiry + timedelta(days=29)

    def test_unmatched_payment_not_credited_but_recorded(self, db_session):
        w = _make_watcher([_transfer_log(from_addr=_PAYER)])
        credited = w.check_new_payments(db_session)
        db_session.flush()

        assert credited == 0
        payment = db_session.query(ProPaymentRow).one()
        assert payment.matched is False

    def test_underpayment_not_credited(self, db_session):
        row = SubscriptionRow(channel="telegram", recipient_id="42", registered_wallet=_PAYER)
        db_session.add(row)
        db_session.flush()

        w = _make_watcher([_transfer_log(amount_usdc=5.0)])
        credited = w.check_new_payments(db_session)
        db_session.flush()

        assert credited == 0
        assert row.is_pro is False


class TestPricingTiers:
    """1mo=$29, 6mo=$29*6*0.95=$165.30 (180d), 12mo=$29*12*0.90=$313.20 (365d)."""

    def test_one_month_amount_grants_30_days(self, db_session):
        row = SubscriptionRow(channel="telegram", recipient_id="42", registered_wallet=_PAYER)
        db_session.add(row)
        db_session.flush()

        w = _make_watcher([_transfer_log(amount_usdc=29.0)])
        w.check_new_payments(db_session)
        db_session.flush()

        assert row.pro_expires_at < datetime.now(tz=timezone.utc) + timedelta(days=31)
        assert row.pro_expires_at > datetime.now(tz=timezone.utc) + timedelta(days=29)

    def test_six_month_amount_grants_180_days(self, db_session):
        row = SubscriptionRow(channel="telegram", recipient_id="42", registered_wallet=_PAYER)
        db_session.add(row)
        db_session.flush()

        w = _make_watcher([_transfer_log(amount_usdc=165.30)])
        w.check_new_payments(db_session)
        db_session.flush()

        assert row.pro_expires_at > datetime.now(tz=timezone.utc) + timedelta(days=179)
        assert row.pro_expires_at < datetime.now(tz=timezone.utc) + timedelta(days=181)

    def test_twelve_month_amount_grants_365_days(self, db_session):
        row = SubscriptionRow(channel="telegram", recipient_id="42", registered_wallet=_PAYER)
        db_session.add(row)
        db_session.flush()

        w = _make_watcher([_transfer_log(amount_usdc=313.20)])
        w.check_new_payments(db_session)
        db_session.flush()

        assert row.pro_expires_at > datetime.now(tz=timezone.utc) + timedelta(days=364)
        assert row.pro_expires_at < datetime.now(tz=timezone.utc) + timedelta(days=366)

    def test_overpayment_matches_longer_tier_not_shortest(self, db_session):
        """Sending enough to clear the 6mo tier must credit 180 days, not just 30."""
        row = SubscriptionRow(channel="telegram", recipient_id="42", registered_wallet=_PAYER)
        db_session.add(row)
        db_session.flush()

        w = _make_watcher([_transfer_log(amount_usdc=200.0)])
        w.check_new_payments(db_session)
        db_session.flush()

        assert row.pro_expires_at > datetime.now(tz=timezone.utc) + timedelta(days=179)


class TestCursorCommit:
    def test_cursor_not_advanced_until_commit_cursor_called(self, db_session):
        w = _make_watcher([])
        w.check_new_payments(db_session)
        assert w._redis.get(watcher_mod._REDIS_LAST_BLOCK_KEY) is None

        w.commit_cursor()
        assert w._redis.get(watcher_mod._REDIS_LAST_BLOCK_KEY) == "1000"

    def test_scan_range_is_capped_at_max_block_span(self, db_session, monkeypatch):
        monkeypatch.setattr(watcher_mod, "PRO_TIER_MAX_BLOCK_SPAN", 100)
        r = fakeredis.FakeStrictRedis(decode_responses=True)
        r.set(watcher_mod._REDIS_LAST_BLOCK_KEY, 0)  # last_checked=0 -> from_block=1
        w = ProPaymentWatcher(redis_client=r)
        fake_w3 = MagicMock()
        fake_w3.eth.block_number = 100_000  # huge outage — far beyond safe_head
        fake_w3.eth.get_logs.return_value = []
        w._w3 = fake_w3

        w.check_new_payments(db_session)

        called_range = fake_w3.eth.get_logs.call_args[0][0]
        assert called_range["fromBlock"] == 1
        assert called_range["toBlock"] == 100  # capped, not the full outage span


class TestExpirySweep:
    def test_expired_pro_reverts_to_free(self, db_session):
        row = SubscriptionRow(
            channel="telegram", recipient_id="1", is_pro=True,
            pro_expires_at=datetime.now(tz=timezone.utc) - timedelta(days=1),
        )
        db_session.add(row)
        db_session.flush()

        swept = sweep_expired_pro(db_session)
        db_session.flush()

        assert swept == 1
        assert row.is_pro is False

    def test_null_expiry_never_touched(self, db_session):
        """NULL pro_expires_at = manual/grandfathered grant — never expires."""
        row = SubscriptionRow(channel="telegram", recipient_id="2", is_pro=True, pro_expires_at=None)
        db_session.add(row)
        db_session.flush()

        swept = sweep_expired_pro(db_session)
        db_session.flush()

        assert swept == 0
        assert row.is_pro is True

    def test_future_expiry_not_swept(self, db_session):
        row = SubscriptionRow(
            channel="telegram", recipient_id="3", is_pro=True,
            pro_expires_at=datetime.now(tz=timezone.utc) + timedelta(days=5),
        )
        db_session.add(row)
        db_session.flush()

        swept = sweep_expired_pro(db_session)

        assert swept == 0
        assert row.is_pro is True
