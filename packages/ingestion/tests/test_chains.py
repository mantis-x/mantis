"""Tests for the chain registry, focused on the new HASHKEY entry."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import os as _os

from src.chains import ALL_CHAINS, HASHKEY, get_enabled_chains


class TestHashKeyChainConfig:
    def test_registered_in_all_chains(self):
        assert "hashkey" in ALL_CHAINS
        assert ALL_CHAINS["hashkey"] is HASHKEY

    def test_chain_id(self):
        assert HASHKEY.chain_id == 177

    def test_no_poa_middleware(self):
        assert HASHKEY.poa_middleware is False

    def test_pool_registry_covers_three_tokens(self):
        assert len(HASHKEY.pool_registry) == 3

    def test_pool_registry_keys_are_lowercase(self):
        for addr in HASHKEY.pool_registry:
            assert addr == addr.lower()

    def test_token_prices_has_native_and_monitored_tokens(self):
        assert "hsk" in HASHKEY.token_prices
        assert "usdt" in HASHKEY.token_prices
        assert "weth" in HASHKEY.token_prices

    def test_no_price_feeds_configured(self):
        # No verified Chainlink deployment found on HashKey — static only.
        assert HASHKEY.price_feeds == {}

    def test_explorer_tx_url(self):
        url = HASHKEY.explorer_tx("0xabc123")
        assert url == "https://hsk.blockscout.com/tx/0xabc123"

    def test_explorer_address_url(self):
        url = HASHKEY.explorer_address("0xdef456")
        assert url == "https://hsk.blockscout.com/address/0xdef456"


class TestGetEnabledChainsIncludesHashKey:
    def test_hashkey_enabled_via_env_var(self, monkeypatch):
        monkeypatch.setenv("CHAINS", "mantle,arbitrum,hashkey")
        chains = get_enabled_chains()
        names = {c.name for c in chains}
        assert names == {"mantle", "arbitrum", "hashkey"}

    def test_hashkey_alone(self, monkeypatch):
        monkeypatch.setenv("CHAINS", "hashkey")
        chains = get_enabled_chains()
        assert len(chains) == 1
        assert chains[0].name == "hashkey"
