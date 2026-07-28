"""
ApiPaymentWatcher tests against a real Postgres (conftest) + fakeredis, web3
mocked. Exercises crediting/idempotency/tier-matching/expiry-stacking — the
API-tier sibling of test_pro_payment_watcher.py.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import fakeredis
import pytest

import src.billing.api_payment_watcher as watcher_mod
from src.billing.api_payment_watcher import ApiPaymentWatcher
from src.db.models.api_customer import ApiCustomerRow
from src.db.models.api_payment import ApiPaymentRow

_RECEIVE = "0x1111111111111111111111111111111111111111"
_PAYER   = "0x2222222222222222222222222222222222222222"


def _transfer_log(*, from_addr=_PAYER, amount_usdc=299.0, tx_hash="0xaaa", log_index=0):
    to_topic = MagicMock(); to_topic.hex.return_value = "0x" + "0" * 24 + _RECEIVE[2:]
    from_topic = MagicMock(); from_topic.hex.return_value = "0x" + "0" * 24 + from_addr[2:]
    tx = MagicMock(); tx.hex.return_value = tx_hash
    data = MagicMock(); data.hex.return_value = hex(int(amount_usdc * 10**6))
    return {"transactionHash": tx, "logIndex": log_index,
            "topics": [MagicMock(), from_topic, to_topic], "data": data}


@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    monkeypatch.setattr(watcher_mod, "API_TIER_PAYMENTS_ENABLED", True)
    monkeypatch.setattr(watcher_mod, "API_TIER_RECEIVE_ADDRESS", _RECEIVE)
    monkeypatch.setattr(watcher_mod, "API_TIER_PRICE_USDC", 299.0)
    monkeypatch.setattr(watcher_mod, "API_TIER_DISCOUNT_6MO_PCT", 5.0)
    monkeypatch.setattr(watcher_mod, "API_TIER_DISCOUNT_12MO_PCT", 10.0)
    monkeypatch.setattr(watcher_mod, "API_TIER_CONFIRMATIONS", 0)


def _watcher():
    return ApiPaymentWatcher(redis_client=fakeredis.FakeStrictRedis(decode_responses=True))


def _customer(session, wallet=_PAYER, expires=None):
    c = ApiCustomerRow(label="c", registered_wallet=wallet, api_tier_expires_at=expires)
    session.add(c); session.flush()
    return c


class TestDisabled:
    def test_no_rpc_when_disabled(self, db_session, monkeypatch):
        monkeypatch.setattr(watcher_mod, "API_TIER_PAYMENTS_ENABLED", False)
        w = _watcher(); w._w3 = MagicMock()
        assert w.check_new_payments(db_session) == 0
        w._w3.eth.get_logs.assert_not_called()


def test_matched_credit_sets_expiry(db_session):
    c = _customer(db_session)
    w = _watcher()
    assert w._process_transfer(db_session, _transfer_log(amount_usdc=299.0)) == 1
    db_session.flush()
    delta = (c.api_tier_expires_at - datetime.now(tz=timezone.utc)).days
    assert 29 <= delta <= 30
    pay = db_session.query(ApiPaymentRow).one()
    assert pay.matched and pay.credited_customer_id == c.id


def test_idempotent_reprocess(db_session):
    _customer(db_session)
    w = _watcher()
    log = _transfer_log(tx_hash="0xdup", log_index=3)
    assert w._process_transfer(db_session, log) == 1
    db_session.flush()
    assert w._process_transfer(db_session, log) == 0          # same tx/log — not re-credited
    assert db_session.query(ApiPaymentRow).count() == 1


def test_expiry_stacks_from_existing(db_session):
    future = datetime.now(tz=timezone.utc) + timedelta(days=10)
    c = _customer(db_session, expires=future)
    w = _watcher()
    w._process_transfer(db_session, _transfer_log(amount_usdc=299.0))
    db_session.flush()
    # 30 days added on top of the existing 10, not from now
    assert (c.api_tier_expires_at - future).days == 30


def test_unmatched_wallet_not_credited(db_session):
    _customer(db_session, wallet="0x9999999999999999999999999999999999999999")
    w = _watcher()
    assert w._process_transfer(db_session, _transfer_log(from_addr=_PAYER)) == 0
    db_session.flush()
    pay = db_session.query(ApiPaymentRow).one()
    assert pay.matched is False and pay.credited_customer_id is None


def test_underpayment_ignored(db_session):
    _customer(db_session)
    w = _watcher()
    assert w._process_transfer(db_session, _transfer_log(amount_usdc=100.0)) == 0


def test_tiers_and_overpayment_favors_longer(db_session):
    # 12mo price = 299*12*0.9 = 3229.20 ; 6mo = 299*6*0.95 = 1704.30
    c = _customer(db_session)
    w = _watcher()
    w._process_transfer(db_session, _transfer_log(amount_usdc=1704.30, tx_hash="0x6mo"))
    db_session.flush()
    assert (c.api_tier_expires_at - datetime.now(tz=timezone.utc)).days >= 179

    c2 = _customer(db_session, wallet="0x3333333333333333333333333333333333333333")
    w._process_transfer(db_session, _transfer_log(
        from_addr="0x3333333333333333333333333333333333333333", amount_usdc=3229.20, tx_hash="0x12mo"))
    db_session.flush()
    assert (c2.api_tier_expires_at - datetime.now(tz=timezone.utc)).days >= 364
