"""
MantleRPCCollector — backward-compatible alias for ChainCollector(MANTLE).

Existing scripts, tests, and documentation that import MantleRPCCollector
continue to work unchanged. New code should use ChainCollector directly.
"""
from __future__ import annotations

from typing import Callable, Optional

from src.chains import MANTLE
from src.collectors.evm_rpc import ChainCollector

# Re-export pool registries so existing scripts/tests don't break.
# Values are PoolMeta objects; .protocol gives the protocol string.
POOL_REGISTRY      = MANTLE.pool_registry
TOKEN_PRICES       = MANTLE.token_prices
AGNI_POOLS         = {k: v for k, v in MANTLE.pool_registry.items() if v.protocol == "agni_finance"}
MERCHANT_MOE_POOLS = {k: v for k, v in MANTLE.pool_registry.items() if v.protocol == "merchant_moe"}
FLUXION_POOLS      = {k: v for k, v in MANTLE.pool_registry.items() if v.protocol == "fluxion"}


class MantleRPCCollector(ChainCollector):
    """Thin alias — creates a ChainCollector pre-configured for Mantle mainnet."""

    def __init__(
        self,
        rpc_url: str = "",
        on_events: Optional[Callable] = None,
        poll_interval: int = 0,
    ):
        import os
        from dataclasses import replace
        config = MANTLE
        if rpc_url or poll_interval:
            config = replace(
                MANTLE,
                poll_interval_s=poll_interval or MANTLE.poll_interval_s,
            )
            if rpc_url:
                # Override via env for ChainCollector to pick up
                os.environ.setdefault("MANTLE_RPC_URL", rpc_url)
        super().__init__(config=config, on_events=on_events)
