"""
Integration tests for SignalOutcomeTracker against a real Postgres
(see conftest.py — requires TEST_DATABASE_URL / defaults to localhost:5433).
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.tracking.signal_outcome_tracker import SignalOutcomeTracker, DIRECTIONAL_SIGNAL_TYPES
from src.db.models.signal import SignalRow
from src.db.models.signal_outcome import SignalOutcomeRow, OutcomeStatus, HORIZONS_HOURS


def _signal_dict(**overrides):
    now = datetime.now(tz=timezone.utc)
    base = {
        "chain":            "arbitrum",
        "protocol":         "uniswap_v3",
        "pool_address":     "0xc6962004f452be9203591991d15f6b388e09e8d0",  # WETH/USDC.e -> "eth"
        "wallets":          ["0xabc", "0xdef"],
        "signal_type":      "accumulation",
        "confidence":       85,
        "summary":          "Test signal",
        "key_factors":      ["factor1", "factor2"],
        "detected_at":      now.isoformat(),
        "deliver_at":       (now + timedelta(minutes=5)).isoformat(),
        "z_score":          3.5,
        "total_volume_usd": 250_000.0,
        "event_type":       "swap",
        "audit_tx_hash":    None,
    }
    base.update(overrides)
    return base


class TestPersistSignal:
    def test_persists_signal_row(self, db_session):
        tracker = SignalOutcomeTracker()
        row = tracker.persist_signal(db_session, _signal_dict())
        db_session.commit()

        assert row.id is not None
        fetched = db_session.query(SignalRow).filter(SignalRow.id == row.id).one()
        assert fetched.chain == "arbitrum"
        assert fetched.protocol == "uniswap_v3"
        assert fetched.confidence == 85
        assert fetched.wallets == ["0xabc", "0xdef"]

    def test_persist_signal_parses_iso_timestamps(self, db_session):
        tracker = SignalOutcomeTracker()
        row = tracker.persist_signal(db_session, _signal_dict())
        assert row.detected_at.tzinfo is not None


class TestScheduleOutcomes:
    def _mock_reader(self, price=1768.39):
        reader = MagicMock()
        reader.get_price.return_value = price
        return reader

    def test_creates_one_outcome_per_horizon(self, db_session):
        tracker = SignalOutcomeTracker(price_reader=self._mock_reader())
        row = tracker.persist_signal(db_session, _signal_dict())
        outcomes = tracker.schedule_outcomes(db_session, row)
        db_session.commit()

        assert len(outcomes) == len(HORIZONS_HOURS)
        assert {o.horizon_label for o in outcomes} == set(HORIZONS_HOURS.keys())

    def test_outcomes_start_pending(self, db_session):
        tracker = SignalOutcomeTracker(price_reader=self._mock_reader())
        row = tracker.persist_signal(db_session, _signal_dict())
        outcomes = tracker.schedule_outcomes(db_session, row)
        assert all(o.status == OutcomeStatus.PENDING for o in outcomes)

    def test_due_at_offsets_from_detected_at(self, db_session):
        tracker = SignalOutcomeTracker(price_reader=self._mock_reader())
        row = tracker.persist_signal(db_session, _signal_dict())
        outcomes = tracker.schedule_outcomes(db_session, row)

        by_label = {o.horizon_label: o for o in outcomes}
        expected_1h = row.detected_at + timedelta(hours=1)
        assert abs((by_label["1h"].due_at - expected_1h).total_seconds()) < 1

    def test_entry_price_captured_from_reader(self, db_session):
        reader = self._mock_reader(price=1768.39)
        tracker = SignalOutcomeTracker(price_reader=reader)
        row = tracker.persist_signal(db_session, _signal_dict())
        outcomes = tracker.schedule_outcomes(db_session, row)

        assert all(o.entry_price_usd == 1768.39 for o in outcomes)
        reader.get_price.assert_called_with("arbitrum", "eth")

    def test_price_key_resolved_from_pool_registry(self, db_session):
        # WBTC/WETH pool -> "wbtc" is the tracked leg, not "eth"
        reader = self._mock_reader()
        tracker = SignalOutcomeTracker(price_reader=reader)
        row = tracker.persist_signal(
            db_session,
            _signal_dict(pool_address="0x2f5e87c9312fa29aed5c179e456625d79015299c"),
        )
        outcomes = tracker.schedule_outcomes(db_session, row)
        assert all(o.price_key == "wbtc" for o in outcomes)


class TestCheckDueOutcomes:
    def test_processes_only_due_outcomes(self, db_session):
        reader = MagicMock()
        reader.get_price.return_value = 1800.0
        tracker = SignalOutcomeTracker(price_reader=reader)

        row = tracker.persist_signal(db_session, _signal_dict())
        tracker.schedule_outcomes(db_session, row)
        db_session.commit()

        # Nothing is due yet (earliest horizon is +1h)
        processed = tracker.check_due_outcomes(db_session, now=datetime.now(tz=timezone.utc))
        assert processed == 0

    def test_processes_outcome_past_due_at(self, db_session):
        reader = MagicMock()
        reader.get_price.return_value = 1800.0
        tracker = SignalOutcomeTracker(price_reader=reader)

        past = datetime.now(tz=timezone.utc) - timedelta(hours=2)
        row = tracker.persist_signal(db_session, _signal_dict(detected_at=past.isoformat()))
        tracker.schedule_outcomes(db_session, row)
        db_session.commit()

        processed = tracker.check_due_outcomes(db_session, now=datetime.now(tz=timezone.utc))
        assert processed >= 1  # at least the 1h horizon is due

    def test_pct_change_computed_correctly(self, db_session):
        reader = MagicMock()
        reader.get_price.side_effect = [1000.0, 1100.0]  # entry, then later check
        tracker = SignalOutcomeTracker(price_reader=reader)

        past = datetime.now(tz=timezone.utc) - timedelta(hours=2)
        row = tracker.persist_signal(db_session, _signal_dict(detected_at=past.isoformat()))
        tracker.schedule_outcomes(db_session, row)
        db_session.commit()

        tracker.check_due_outcomes(db_session, now=datetime.now(tz=timezone.utc))
        db_session.commit()

        outcome_1h = (
            db_session.query(SignalOutcomeRow)
            .filter(SignalOutcomeRow.signal_id == row.id, SignalOutcomeRow.horizon_label == "1h")
            .one()
        )
        assert outcome_1h.status == OutcomeStatus.COMPLETED
        assert abs(outcome_1h.pct_change - 10.0) < 0.01  # (1100-1000)/1000 * 100

    def test_price_lookup_failure_marks_single_outcome_failed(self, db_session):
        reader = MagicMock()
        reader.get_price.side_effect = [1000.0, Exception("rpc down"), 1000.0, 1000.0, 1000.0]
        tracker = SignalOutcomeTracker(price_reader=reader)

        past = datetime.now(tz=timezone.utc) - timedelta(days=8)
        row = tracker.persist_signal(db_session, _signal_dict(detected_at=past.isoformat()))
        tracker.schedule_outcomes(db_session, row)
        db_session.commit()

        # All 4 horizons are due; one will raise via side_effect
        tracker.check_due_outcomes(db_session, now=datetime.now(tz=timezone.utc))
        db_session.commit()

        statuses = {
            o.status for o in
            db_session.query(SignalOutcomeRow).filter(SignalOutcomeRow.signal_id == row.id).all()
        }
        assert OutcomeStatus.FAILED in statuses
        assert OutcomeStatus.COMPLETED in statuses  # the others still succeeded


class TestDirectionalSignalTypes:
    def test_accumulation_and_whale_entry_imply_up(self):
        assert DIRECTIONAL_SIGNAL_TYPES["accumulation"] == "up"
        assert DIRECTIONAL_SIGNAL_TYPES["whale_entry"] == "up"

    def test_distribution_and_whale_exit_imply_down(self):
        assert DIRECTIONAL_SIGNAL_TYPES["distribution"] == "down"
        assert DIRECTIONAL_SIGNAL_TYPES["whale_exit"] == "down"

    def test_unusual_volume_has_no_directional_claim(self):
        assert "unusual_volume" not in DIRECTIONAL_SIGNAL_TYPES
