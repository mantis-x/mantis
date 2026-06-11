"""Tests for the signal card formatter."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.formatters.signal_card import (
    format_signal_card,
    format_status_card,
    format_history_card,
    confidence_bar,
    format_usd,
)


def make_signal(**kwargs):
    base = {
        "signal_type":     "accumulation",
        "protocol":        "agni_finance",
        "pool_address":    "0xcda86a272531e8640cd7f1a92c01839911b90bb0",
        "wallets":         ["0xabc123", "0xdef456"],
        "confidence":      82,
        "summary":         "Smart money accumulated USDY on Agni Finance. Volume is 3.8σ above the 14-day baseline.",
        "key_factors":     ["Multi-wallet cluster", "High z-score", "mETH pool"],
        "z_score":         3.8,
        "total_volume_usd": 250_000.0,
        "event_type":      "swap",
    }
    base.update(kwargs)
    return base


def test_signal_card_contains_protocol():
    card = format_signal_card(make_signal())
    assert "Agni Finance" in card


def test_signal_card_contains_confidence():
    card = format_signal_card(make_signal(confidence=82))
    assert "82%" in card


def test_signal_card_contains_summary():
    card = format_signal_card(make_signal())
    assert "Smart money accumulated" in card


def test_signal_card_contains_key_factors():
    card = format_signal_card(make_signal())
    assert "Multi-wallet cluster" in card


def test_signal_card_with_audit_hash():
    card = format_signal_card(make_signal(), audit_tx_hash="0xdeadbeef")
    assert "verify signal" in card
    assert "0xdeadbeef" in card


def test_signal_card_without_audit_hash():
    card = format_signal_card(make_signal(), audit_tx_hash=None)
    assert "Pool" in card


def test_whale_entry_emoji():
    card = format_signal_card(make_signal(signal_type="whale_entry"))
    assert "🐋" in card


def test_confidence_bar_high():
    bar = confidence_bar(90)
    assert "🟢" in bar
    assert "90%" in bar


def test_confidence_bar_medium():
    bar = confidence_bar(65)
    assert "🟡" in bar


def test_confidence_bar_low():
    bar = confidence_bar(40)
    assert "🔴" in bar


def test_format_usd_millions():
    assert format_usd(1_500_000) == "$1.5M"


def test_format_usd_thousands():
    assert format_usd(250_000) == "$250K"


def test_format_usd_small():
    assert format_usd(500) == "$500"


def test_status_card():
    card = format_status_card({"signals_today": 3, "candidates": 12, "pools": 11})
    assert "Mantis Scout" in card
    assert "11" in card


def test_history_card_empty():
    card = format_history_card([])
    assert "No signals" in card


def test_history_card_with_signals():
    signals = [make_signal(), make_signal(signal_type="whale_entry", confidence=90)]
    card    = format_history_card(signals)
    assert "Accumulation" in card
    assert "Whale Entry" in card


if __name__ == "__main__":
    test_signal_card_contains_protocol()
    test_signal_card_contains_confidence()
    test_signal_card_contains_summary()
    test_signal_card_contains_key_factors()
    test_signal_card_with_audit_hash()
    test_signal_card_without_audit_hash()
    test_whale_entry_emoji()
    test_confidence_bar_high()
    test_confidence_bar_medium()
    test_confidence_bar_low()
    test_format_usd_millions()
    test_format_usd_thousands()
    test_format_usd_small()
    test_status_card()
    test_history_card_empty()
    test_history_card_with_signals()
    print("✓ All formatter tests passed")
