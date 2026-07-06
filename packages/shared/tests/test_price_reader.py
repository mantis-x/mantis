"""Tests for LivePriceReader — Chainlink feed reads with static fallback."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch

from src.pricing.price_reader import LivePriceReader, _STATIC_FALLBACK


def test_unconfigured_price_key_returns_static_fallback():
    reader = LivePriceReader()
    # "mnt" has no Chainlink feed configured for any chain
    price = reader.get_price("mantle", "mnt")
    assert price == _STATIC_FALLBACK["mnt"]


def test_unconfigured_chain_returns_static_fallback():
    reader = LivePriceReader()
    price = reader.get_price("some_unknown_chain", "eth")
    assert price == _STATIC_FALLBACK["eth"]


def test_unknown_price_key_returns_zero():
    reader = LivePriceReader()
    price = reader.get_price("arbitrum", "totally_unknown_token")
    assert price == 0.0


def test_feed_read_success_returns_live_price():
    reader = LivePriceReader()
    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = True
    mock_feed = MagicMock()
    mock_feed.functions.decimals.return_value.call.return_value = 8
    mock_feed.functions.latestRoundData.return_value.call.return_value = (
        1, 176839407000, 0, 0, 1,
    )
    mock_w3.eth.contract.return_value = mock_feed
    reader._w3_by_chain["arbitrum"] = mock_w3

    price = reader.get_price("arbitrum", "eth")
    assert abs(price - 1768.39407) < 0.001


def test_feed_read_failure_falls_back_to_static():
    reader = LivePriceReader()
    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = True
    mock_feed = MagicMock()
    mock_feed.functions.decimals.return_value.call.side_effect = Exception("rpc down")
    mock_w3.eth.contract.return_value = mock_feed
    reader._w3_by_chain["arbitrum"] = mock_w3

    price = reader.get_price("arbitrum", "eth")
    assert price == _STATIC_FALLBACK["eth"]


def test_w3_connection_cached_per_chain():
    reader = LivePriceReader()
    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = True
    mock_feed = MagicMock()
    mock_feed.functions.decimals.return_value.call.return_value = 8
    mock_feed.functions.latestRoundData.return_value.call.return_value = (1, 100_00000000, 0, 0, 1)
    mock_w3.eth.contract.return_value = mock_feed
    reader._w3_by_chain["arbitrum"] = mock_w3

    reader.get_price("arbitrum", "eth")
    reader.get_price("arbitrum", "usdc")
    # _get_w3 should not have tried to reconnect — same cached instance used
    assert reader._w3_by_chain["arbitrum"] is mock_w3
