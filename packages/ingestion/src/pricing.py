"""
PriceOracle — live token prices for USD estimation in the event normaliser.

ChainConfig.token_prices is a static fallback dict. Wherever a chain has a
verified Chainlink feed configured (ChainConfig.price_feeds), PriceOracle
reads it live and overlays the result; any price_key without a configured
feed, or whose feed read fails, keeps its static fallback value.

Prices are cached for CACHE_TTL_S to avoid a Chainlink read on every poll
tick — ingestion ticks every 10-15s per chain, prices don't need to be
that fresh.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

CACHE_TTL_S = 60

_FEED_ABI = [
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"name": "roundId",         "type": "uint80"},
            {"name": "answer",          "type": "int256"},
            {"name": "startedAt",       "type": "uint256"},
            {"name": "updatedAt",       "type": "uint256"},
            {"name": "answeredInRound", "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
]


class PriceOracle:
    """
    Live price lookup for one chain, backed by Chainlink feeds where
    configured and a static fallback dict otherwise.
    """

    def __init__(self, w3, config):
        """
        w3:     a connected Web3 instance for the chain config's RPC.
        config: a ChainConfig (uses .token_prices and .price_feeds).
        """
        self._w3     = w3
        self._config = config
        self._feeds  = {}

        from web3 import Web3
        for price_key, address in config.price_feeds.items():
            try:
                self._feeds[price_key] = w3.eth.contract(
                    address=Web3.to_checksum_address(address),
                    abi=_FEED_ABI,
                )
            except Exception as exc:
                log.warning(
                    "PriceOracle[%s]: could not load feed for %s (%s): %s",
                    config.name, price_key, address, exc,
                )

        self._cache: dict = {}
        self._cache_ts = 0.0

    def get_prices(self) -> dict:
        """Return {price_key: usd_price}, live where available, else static."""
        now = time.time()
        if self._cache and now - self._cache_ts < CACHE_TTL_S:
            return self._cache

        prices = dict(self._config.token_prices)
        for price_key, feed in self._feeds.items():
            try:
                decimals = feed.functions.decimals().call()
                _, answer, _, _, _ = feed.functions.latestRoundData().call()
                prices[price_key] = answer / (10 ** decimals)
            except Exception as exc:
                log.warning(
                    "PriceOracle[%s]: feed read failed for %s: %s — using static fallback",
                    self._config.name, price_key, exc,
                )

        self._cache    = prices
        self._cache_ts = now
        return prices
