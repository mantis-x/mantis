"""
LivePriceReader — minimal "current price for a price_key on a chain"
lookup, used by the signal outcome tracker to snapshot entry price and
later re-check price at each horizon.

Mirrors packages/ingestion/src/pricing.py's PriceOracle (same verified
Chainlink feed addresses, same static fallback dict) rather than importing
it directly — see pool_registry.py's module docstring for why cross-package
imports aren't viable in this repo's structure. Every feed address below
was verified on-chain (get_code + the feed's own description()) before
being added; see the ingestion package's chains.py for that verification
history.

Unlike PriceOracle, this has no 60s cache — the tracker calls it at most
once per signal (entry) and once per due horizon check (a few times a day
per pending outcome), so caching isn't needed and would only risk serving
a stale price for a "point in time" snapshot that's supposed to be exact.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

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

# chain -> {price_key: feed_address}. Arbitrum One mainnet, verified on-chain.
_CHAINLINK_FEEDS: dict[str, dict[str, str]] = {
    "arbitrum": {
        "eth":  "0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612",
        "wbtc": "0x6ce185860a4963106506C203335A2910413708e9",
        "arb":  "0xb2A824043730FE05F3DA2efaFa1CBbe83fa548D6",
        "usdc": "0x50834F3163758fcC1Df9973b6e91f0F0F0434aD3",
        "usdt": "0x3f3f5dF88dC9F13eac63DF89EC16ef6e7E25DdE7",
    },
    # Mantle: no verified official Chainlink deployment — static fallback only.
}

# Static fallback, used when no feed is configured/reachable for a price_key.
_STATIC_FALLBACK: dict[str, float] = {
    "eth": 2400.0, "wbtc": 65000.0, "arb": 0.80, "usdc": 1.0, "usdt": 1.0,
    "mnt": 0.72, "weth": 2400.0, "meth": 2450.0,
}

_RPC_URLS = {
    "arbitrum": os.getenv("ARBITRUM_RPC_URL", "https://arb1.arbitrum.io/rpc"),
    "mantle":   os.getenv("MANTLE_RPC_URL", "https://rpc.mantle.xyz"),
}


class LivePriceReader:
    """Fetches the current USD price for a price_key on a chain, with a static fallback."""

    def __init__(self):
        self._w3_by_chain: dict = {}

    def _get_w3(self, chain: str):
        if chain not in self._w3_by_chain:
            from web3 import Web3
            rpc_url = _RPC_URLS.get(chain)
            if not rpc_url:
                self._w3_by_chain[chain] = None
                return None
            try:
                w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))
                self._w3_by_chain[chain] = w3 if w3.is_connected() else None
            except Exception as exc:
                log.warning("LivePriceReader: could not connect to %s RPC: %s", chain, exc)
                self._w3_by_chain[chain] = None
        return self._w3_by_chain[chain]

    def get_price(self, chain: str, price_key: str) -> float:
        """Return the current USD price for price_key on chain. Never raises."""
        feed_addr = _CHAINLINK_FEEDS.get(chain, {}).get(price_key)
        if feed_addr:
            w3 = self._get_w3(chain)
            if w3 is not None:
                try:
                    from web3 import Web3
                    feed = w3.eth.contract(
                        address=Web3.to_checksum_address(feed_addr), abi=_FEED_ABI
                    )
                    decimals = feed.functions.decimals().call()
                    _, answer, _, _, _ = feed.functions.latestRoundData().call()
                    return answer / (10 ** decimals)
                except Exception as exc:
                    log.warning(
                        "LivePriceReader: feed read failed for %s/%s: %s — using static fallback",
                        chain, price_key, exc,
                    )
        return _STATIC_FALLBACK.get(price_key, 0.0)
