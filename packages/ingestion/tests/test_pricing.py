"""
Unit tests for PriceOracle — live Chainlink prices overlaid onto the
static ChainConfig.token_prices fallback dict.
"""
import time
from unittest.mock import MagicMock

import pytest

from src.pricing import PriceOracle, CACHE_TTL_S
from src.chains import ChainConfig


def _make_config(token_prices: dict, price_feeds: dict) -> ChainConfig:
    return ChainConfig(
        name="testchain",
        chain_id=1,
        rpc_env="TEST_RPC_URL",
        default_rpc="https://example.invalid",
        explorer_base="https://example.invalid",
        poa_middleware=False,
        poll_interval_s=10,
        max_blocks_per_batch=100,
        native_token="test",
        token_prices=token_prices,
        price_feeds=price_feeds,
    )


def _make_feed_contract(price: float, decimals: int = 8):
    contract = MagicMock()
    contract.functions.decimals.return_value.call.return_value = decimals
    answer = int(price * 10 ** decimals)
    contract.functions.latestRoundData.return_value.call.return_value = (
        1, answer, 0, 0, 1,
    )
    return contract


class TestPriceOracleNoFeeds:
    """No price_feeds configured (e.g. Mantle) → static prices pass through unchanged."""

    def test_returns_static_prices_unchanged(self):
        config = _make_config(token_prices={"mnt": 0.72, "usdc": 1.0}, price_feeds={})
        oracle = PriceOracle(w3=MagicMock(), config=config)
        prices = oracle.get_prices()
        assert prices == {"mnt": 0.72, "usdc": 1.0}

    def test_does_not_mutate_original_static_dict(self):
        static = {"mnt": 0.72}
        config = _make_config(token_prices=static, price_feeds={})
        oracle = PriceOracle(w3=MagicMock(), config=config)
        prices = oracle.get_prices()
        prices["mnt"] = 999.0
        assert static["mnt"] == 0.72  # original untouched


class TestPriceOracleWithFeeds:
    """price_feeds configured → live value overlays the static fallback."""

    def _make_oracle_with_feed(self, static_price=2400.0, live_price=1768.39):
        config = _make_config(
            token_prices={"eth": static_price, "usdc": 1.0},
            price_feeds={"eth": "0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612"},
        )
        mock_w3 = MagicMock()
        mock_w3.eth.contract.return_value = _make_feed_contract(live_price)
        oracle = PriceOracle(w3=mock_w3, config=config)
        return oracle

    def test_live_price_overlays_static(self):
        oracle = self._make_oracle_with_feed(static_price=2400.0, live_price=1768.39)
        prices = oracle.get_prices()
        assert abs(prices["eth"] - 1768.39) < 0.01

    def test_key_without_feed_keeps_static_value(self):
        oracle = self._make_oracle_with_feed()
        prices = oracle.get_prices()
        assert prices["usdc"] == 1.0  # no feed configured for usdc in this test

    def test_caches_within_ttl(self):
        oracle = self._make_oracle_with_feed(live_price=1768.39)
        first = oracle.get_prices()
        # Change what the mocked feed would return — cached call must not see it
        oracle._feeds["eth"] = _make_feed_contract(9999.0)
        second = oracle.get_prices()
        assert first["eth"] == second["eth"] == pytest.approx(1768.39, rel=0.001)

    def test_refreshes_after_ttl_expires(self):
        oracle = self._make_oracle_with_feed(live_price=1768.39)
        oracle.get_prices()
        oracle._cache_ts = time.time() - CACHE_TTL_S - 1  # force expiry
        oracle._feeds["eth"] = _make_feed_contract(1800.0)
        refreshed = oracle.get_prices()
        assert abs(refreshed["eth"] - 1800.0) < 0.01


class TestPriceOracleFeedFailure:
    """A feed read failure must fall back to the static value, not raise."""

    def test_falls_back_to_static_on_call_error(self):
        config = _make_config(
            token_prices={"arb": 0.80},
            price_feeds={"arb": "0xb2A824043730FE05F3DA2efaFa1CBbe83fa548D6"},
        )
        mock_w3 = MagicMock()
        broken_feed = MagicMock()
        broken_feed.functions.decimals.return_value.call.side_effect = Exception("rpc down")
        mock_w3.eth.contract.return_value = broken_feed
        oracle = PriceOracle(w3=mock_w3, config=config)

        prices = oracle.get_prices()
        assert prices["arb"] == 0.80

    def test_partial_failure_only_affects_broken_feed(self):
        """One feed failing must not affect prices for other keys."""
        config = _make_config(
            token_prices={"eth": 2400.0, "arb": 0.80},
            price_feeds={
                "eth": "0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612",
                "arb": "0xb2A824043730FE05F3DA2efaFa1CBbe83fa548D6",
            },
        )
        good_feed   = _make_feed_contract(1768.39)
        broken_feed = MagicMock()
        broken_feed.functions.decimals.return_value.call.side_effect = Exception("rpc down")

        mock_w3 = MagicMock()
        mock_w3.eth.contract.side_effect = [good_feed, broken_feed]
        oracle = PriceOracle(w3=mock_w3, config=config)

        prices = oracle.get_prices()
        assert abs(prices["eth"] - 1768.39) < 0.01
        assert prices["arb"] == 0.80  # fell back to static


class TestPriceOracleFeedLoadFailure:
    """If a feed contract can't even be instantiated, oracle must still work."""

    def test_survives_bad_feed_address_at_init(self):
        config = _make_config(
            token_prices={"eth": 2400.0},
            price_feeds={"eth": "not-a-valid-address"},
        )
        mock_w3 = MagicMock()
        mock_w3.eth.contract.side_effect = Exception("invalid address")
        oracle = PriceOracle(w3=mock_w3, config=config)  # must not raise

        prices = oracle.get_prices()
        assert prices["eth"] == 2400.0  # no feed loaded -> static fallback
