"""Tests for the intent rule engine."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.intent.rule_engine import RuleEngine


def make_signal(**kwargs):
    base = {
        "id":              1,
        "signal_type":     "accumulation",
        "protocol":        "agni_finance",
        "pool_address":    "0xcda86...",
        "confidence":      82,
        "z_score":         3.8,
        "total_volume_usd": 250_000,
        "event_type":      "swap",
    }
    base.update(kwargs)
    return base


def make_rules(**kwargs):
    base = {
        "min_confidence": 75,
        "signal_types":   ["accumulation", "whale_entry"],
        "protocols":      ["agni_finance", "merchant_moe"],
        "min_z_score":    3.0,
        "action":         "swap",
        "amount_usd":     100.0,
    }
    base.update(kwargs)
    return base


def test_matching_signal_returns_request():
    engine  = RuleEngine()
    request = engine.evaluate(make_signal(), 0, make_rules(), "0xowner")
    assert request is not None
    assert request.action_type.value == "swap"
    assert request.amount_usd == 100.0


def test_low_confidence_returns_none():
    engine  = RuleEngine()
    signal  = make_signal(confidence=50)
    request = engine.evaluate(signal, 0, make_rules(min_confidence=75), "0xowner")
    assert request is None


def test_wrong_signal_type_returns_none():
    engine  = RuleEngine()
    signal  = make_signal(signal_type="distribution")
    request = engine.evaluate(signal, 0, make_rules(signal_types=["accumulation"]), "0xowner")
    assert request is None


def test_wrong_protocol_returns_none():
    engine  = RuleEngine()
    signal  = make_signal(protocol="fluxion")
    request = engine.evaluate(signal, 0, make_rules(protocols=["agni_finance"]), "0xowner")
    assert request is None


def test_low_z_score_returns_none():
    engine  = RuleEngine()
    signal  = make_signal(z_score=2.0)
    request = engine.evaluate(signal, 0, make_rules(min_z_score=3.0), "0xowner")
    assert request is None


def test_no_type_filter_matches_any():
    engine  = RuleEngine()
    rules   = make_rules()
    del rules["signal_types"]
    signal  = make_signal(signal_type="whale_exit")
    request = engine.evaluate(signal, 0, rules, "0xowner")
    assert request is not None


def test_no_protocol_filter_matches_any():
    engine  = RuleEngine()
    rules   = make_rules()
    del rules["protocols"]
    signal  = make_signal(protocol="fluxion")
    request = engine.evaluate(signal, 0, rules, "0xowner")
    assert request is not None


if __name__ == "__main__":
    test_matching_signal_returns_request()
    test_low_confidence_returns_none()
    test_wrong_signal_type_returns_none()
    test_wrong_protocol_returns_none()
    test_low_z_score_returns_none()
    test_no_type_filter_matches_any()
    test_no_protocol_filter_matches_any()
    print("✓ All rule engine tests passed")
