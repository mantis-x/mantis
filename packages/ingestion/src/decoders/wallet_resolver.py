"""
WalletResolver — resolve the real beneficiary EOA behind pool events that were
routed through a known intermediary contract.

Why this exists (found 2026-07-21 via production data): Uniswap V3 `Mint`/`Burn`
events attribute the position to `owner` = the NonfungiblePositionManager (NFPM)
contract when liquidity is added through it, and `Swap` attributes to
`sender` = the calling router — **not** the end user. On Arbitrum/Ethereum this
collapsed nearly every distinct actor into one contract address
(`0xc36442…` = the NFPM), which is fatal for multi-wallet clustering: two
different users LP-ing through the NFPM look like the same wallet.

The pool event itself never contains the user's EOA — the only reliable
"real beneficiary" is `tx.origin` (the address that signed the transaction).
When an event's attributed wallet is one of these known proxies, we replace it
with `tx.origin`, resolved via one `eth_getTransaction` call cached per tx hash.

Cost control: resolution only fires for events attributed to a KNOWN proxy, so
direct-EOA interactions (and GMX perps / HashKey transfers, which already
attribute to the real actor) cost nothing extra. Gated by RESOLVE_TX_ORIGIN
(default on) so it can be disabled if a chain's public RPC is under pressure —
see PROJECT_STATE's Ethereum/Arbitrum rate-limit notes.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

# Known intermediary contracts whose address is NOT the end user. Lowercased.
# These canonical Uniswap deployments share the same address across Ethereum and
# Arbitrum. Including an address that never appears is harmless (no real event is
# attributed to it); the only real risk is *omitting* one, so treat this as a
# living list and extend via EXTRA_PROXY_ADDRESSES without a code change.
_BUILTIN_PROXIES = {
    "0xc36442b4a4522e871399cd717abdd847ab11fe88",  # Uniswap V3 NonfungiblePositionManager (ETH + ARB)
    "0xe592427a0aece92de3edee1f18e0157c05861564",  # Uniswap V3 SwapRouter (ETH + ARB)
    "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45",  # Uniswap V3 SwapRouter02 (ETH + ARB)
    "0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad",  # Uniswap Universal Router (ETH)
    "0x66a9893cc07d91d95644aedd05d03f95e1dba8af",  # Uniswap Universal Router v1.2 (ETH)
    "0x5e325eda8064b456f4781070c0738d849c824258",  # Uniswap Universal Router (Arbitrum)
    "0xa51afafe0263b40edaef0df8781ea9aa03e381a3",  # Uniswap Universal Router (Arbitrum, newer)
}


def _load_proxies() -> set[str]:
    proxies = set(_BUILTIN_PROXIES)
    extra = os.getenv("EXTRA_PROXY_ADDRESSES", "")
    for addr in extra.split(","):
        addr = addr.strip().lower()
        if addr:
            proxies.add(addr)
    return proxies


class WalletResolver:
    """Replaces proxy-attributed wallet addresses with the transaction's origin EOA."""

    def __init__(self, w3, *, enabled: bool | None = None, cache_max: int = 5000):
        self._w3 = w3
        self._proxies = _load_proxies()
        self._enabled = (
            os.getenv("RESOLVE_TX_ORIGIN", "true").lower() in ("1", "true", "yes")
            if enabled is None else enabled
        )
        self._cache: dict[str, str] = {}   # tx_hash (lower) → origin EOA (lower)
        self._cache_max = cache_max

    def resolve(self, event):
        """If `event.wallet_address` is a known proxy, overwrite it with tx.origin.
        Returns the same event (mutated in place). Never raises — on any RPC
        failure the original proxy address is kept so the pipeline still runs."""
        if not self._enabled or event is None:
            return event

        wallet = (event.wallet_address or "").lower()
        if wallet not in self._proxies:
            return event

        origin = self._tx_origin(event.tx_hash)
        if origin:
            event.wallet_address = origin
        return event

    def _tx_origin(self, tx_hash: str) -> str | None:
        if not tx_hash:
            return None
        key = tx_hash.lower()
        if key in self._cache:
            return self._cache[key]

        try:
            tx = self._w3.eth.get_transaction(tx_hash)
            origin = (tx["from"] or "").lower()
        except Exception as exc:
            log.debug("tx.origin lookup failed for %s: %s", tx_hash[:12], exc)
            return None

        if not origin:
            return None

        if len(self._cache) >= self._cache_max:
            # Drop oldest ~half (dicts preserve insertion order) to bound memory.
            for k in list(self._cache)[: self._cache_max // 2]:
                del self._cache[k]
        self._cache[key] = origin
        return origin
