"""Tests for the (chain, pool_address) -> price_key resolver."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.pricing.pool_registry import resolve_price_key


def test_known_arbitrum_pool_resolves_to_specific_leg():
    # WBTC/WETH pool — WBTC is the tracked (story) leg, not WETH
    assert resolve_price_key("arbitrum", "0x2f5e87c9312fa29aed5c179e456625d79015299c") == "wbtc"


def test_known_arbitrum_weth_usdc_pool_resolves_to_eth():
    assert resolve_price_key("arbitrum", "0xc6962004f452be9203591991d15f6b388e09e8d0") == "eth"


def test_arb_pool_resolves_to_arb_not_weth():
    assert resolve_price_key("arbitrum", "0xc6f780497a95e246eb9449f5e4770916dcd6396a") == "arb"


def test_unknown_arbitrum_pool_falls_back_to_eth():
    assert resolve_price_key("arbitrum", "0xdeadbeef00000000000000000000000000dead") == "eth"


def test_unknown_mantle_pool_falls_back_to_mnt():
    assert resolve_price_key("mantle", "0x0000000000000000000000000000000000dead") == "mnt"


def test_pool_address_case_insensitive():
    upper = "0xC6962004F452BE9203591991D15F6B388E09E8D0"
    assert resolve_price_key("arbitrum", upper) == "eth"


def test_unknown_chain_falls_back_to_eth():
    assert resolve_price_key("some_new_chain", "0xanything") == "eth"
