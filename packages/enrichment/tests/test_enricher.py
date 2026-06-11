"""
Tests for enrichment pipeline.
Tests the parser and prompt builder without making real API calls.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.parsers.llm_response import parse_llm_response
from src.prompts.signal_classifier import build_prompt


# ── Parser tests ─────────────────────────────────────────────────────────────

def test_valid_json_parsed():
    raw = '{"summary": "Smart money accumulated USDY.", "confidence": 82, "signal_type": "accumulation", "key_factors": ["High z-score", "Multi-wallet cluster", "mETH pool"]}'
    result = parse_llm_response(raw)
    assert result is not None
    assert result["confidence"] == 82
    assert result["signal_type"] == "accumulation"
    assert len(result["key_factors"]) == 3


def test_markdown_fences_stripped():
    raw = '```json\n{"summary": "Test.", "confidence": 75, "signal_type": "whale_entry", "key_factors": ["a", "b", "c"]}\n```'
    result = parse_llm_response(raw)
    assert result is not None
    assert result["confidence"] == 75


def test_invalid_signal_type_defaults():
    raw = '{"summary": "Test.", "confidence": 60, "signal_type": "unknown_type", "key_factors": ["a"]}'
    result = parse_llm_response(raw)
    assert result is not None
    assert result["signal_type"] == "unusual_volume"


def test_confidence_clamped():
    raw = '{"summary": "Test.", "confidence": 150, "signal_type": "accumulation", "key_factors": ["a"]}'
    result = parse_llm_response(raw)
    assert result["confidence"] == 100


def test_empty_response_returns_none():
    assert parse_llm_response("") is None
    assert parse_llm_response("   ") is None


def test_invalid_json_returns_none():
    assert parse_llm_response("not json at all") is None
    assert parse_llm_response("{broken json}") is None


def test_missing_summary_returns_none():
    raw = '{"confidence": 80, "signal_type": "accumulation", "key_factors": ["a"]}'
    assert parse_llm_response(raw) is None


# ── Prompt builder tests ──────────────────────────────────────────────────────

def make_candidate(**kwargs):
    base = {
        "wallets":          ["0xabc123", "0xdef456"],
        "pool_address":     "0xcda86a272531e8640cd7f1a92c01839911b90bb0",
        "protocol":         "agni_finance",
        "event_type":       "swap",
        "total_volume_usd": 250_000.0,
        "z_score":          3.8,
        "event_count":      4,
        "signal_type":      "accumulation",
        "first_seen":       "2026-07-01T10:00:00+00:00",
        "last_seen":        "2026-07-01T10:20:00+00:00",
        "detected_at":      "2026-07-01T10:20:00+00:00",
    }
    base.update(kwargs)
    return base


def test_prompt_contains_protocol():
    prompt = build_prompt(make_candidate())
    assert "agni_finance" in prompt


def test_prompt_contains_volume():
    prompt = build_prompt(make_candidate(total_volume_usd=500_000))
    assert "500,000" in prompt


def test_prompt_contains_z_score():
    prompt = build_prompt(make_candidate(z_score=4.2))
    assert "4.2" in prompt


def test_prompt_shows_wallet_count():
    candidate = make_candidate(wallets=["0xaaa", "0xbbb", "0xccc"])
    prompt    = build_prompt(candidate)
    assert "3" in prompt


if __name__ == "__main__":
    test_valid_json_parsed()
    test_markdown_fences_stripped()
    test_invalid_signal_type_defaults()
    test_confidence_clamped()
    test_empty_response_returns_none()
    test_invalid_json_returns_none()
    test_missing_summary_returns_none()
    test_prompt_contains_protocol()
    test_prompt_contains_volume()
    test_prompt_contains_z_score()
    test_prompt_shows_wallet_count()
    print("✓ All enrichment tests passed")
