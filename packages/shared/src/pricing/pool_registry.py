"""
pool_registry — resolves (chain, pool_address) -> price_key for the signal
outcome tracker.

This mirrors the per-pool token metadata already in
packages/ingestion/src/chains.py (PoolMeta.token0/token1). It is a
deliberate copy, not a cross-package import: every package in this repo
uses its own top-level `src` namespace (see each package's worker.py /
sys.path.insert pattern), so two packages' `src` trees cannot be imported
into the same process without colliding. Keep this in sync with
packages/ingestion/src/chains.py whenever a pool is added there.

Resolution order:
  1. Exact (chain, pool_address) match below -> that pool's tracked leg
     (the non-stablecoin token, since that's the asset whose price move
     is the actual signal — a stablecoin "moving" isn't the story).
  2. Chain-level fallback -> the chain's primary volatile asset. Coarser
     (assumes the pool's volatile leg is the chain's native asset, which
     holds for the large majority of registered pools today: Arbitrum
     pools are overwhelmingly WETH-paired, Mantle Agni/Merchant Moe pools
     overwhelmingly WMNT-paired) but always available, so tracking never
     silently drops a signal for lack of a specific mapping.
"""
from __future__ import annotations

# Stablecoins are never the "tracked" leg — accumulation/distribution of a
# stablecoin isn't a directional signal.
_STABLECOINS = {"usdc", "usdt"}

# (chain, pool_address_lowercase) -> price_key
# Mirrors packages/ingestion/src/chains.py ARBITRUM.pool_registry token0/token1.
_POOL_PRICE_KEYS: dict[tuple[str, str], str] = {
    ("arbitrum", "0xc6962004f452be9203591991d15f6b388e09e8d0"): "eth",   # WETH/USDC.e 0.05%
    ("arbitrum", "0xc473e2aee3441bf9240be85eb122abb059a3b57c"): "eth",   # WETH/USDC 0.05%
    ("arbitrum", "0x641c00a822e8b671738d32a431a4fb6074e5c79d"): "eth",   # WETH/USDT 0.05%
    ("arbitrum", "0x2f5e87c9312fa29aed5c179e456625d79015299c"): "wbtc",  # WBTC/WETH 0.3% — WBTC is the story leg
    ("arbitrum", "0xc6f780497a95e246eb9449f5e4770916dcd6396a"): "arb",   # WETH/ARB 0.3% — ARB is the story leg
    ("arbitrum", "0x17c14d2c404d167802b16c450d3c99f88f2c4f4d"): "eth",   # WETH/USDC.e 0.3%
}

# Chain-level fallback: primary volatile asset, used for any pool not in
# _POOL_PRICE_KEYS above (covers Mantle, whose legacy pools carry no
# per-token metadata at all in chains.py, and any new Arbitrum pool added
# to ingestion but not yet mirrored here).
_CHAIN_FALLBACK_PRICE_KEY: dict[str, str] = {
    "mantle":   "mnt",
    "arbitrum": "eth",
}


def resolve_price_key(chain: str, pool_address: str) -> str:
    """
    Return the price_key (e.g. "eth", "mnt", "arb") whose price movement
    represents this pool's signal. Never raises — falls back to the
    chain's primary asset if the pool isn't in the explicit map.
    """
    key = (chain, pool_address.lower())
    if key in _POOL_PRICE_KEYS:
        return _POOL_PRICE_KEYS[key]
    return _CHAIN_FALLBACK_PRICE_KEY.get(chain, "eth")
