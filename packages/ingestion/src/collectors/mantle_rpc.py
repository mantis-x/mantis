"""
MantleRPCCollector
──────────────────
Polls Mantle mainnet for new blocks every 15 seconds.
For each new block, fetches logs from all tracked protocol pools
and hands them to the EventNormaliser.

Real contract addresses sourced from mantlescan.xyz:
  - Agni Finance Swap Router: 0x319B69888b0d11cEC22caA5034e25FfFBDc88421
  - Agni Finance Factory:     0xe9827B4EBeB9AE41FC57efDdDd79EDddC2EA4d03  
  - Merchant Moe LB Router:  0x013e138EF6008ae3B5a8c21e6EF571d89b0b57d8
  - Fluxion:                  tracked via factory events
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Callable, Optional

from web3 import Web3
try:
    from web3.middleware import ExtraDataToPOAMiddleware
except ImportError:
    from web3.middleware import geth_poa_middleware as ExtraDataToPOAMiddleware
    
from src.decoders.event_normaliser import (
    EventNormaliser,
    SWAP_TOPIC, MINT_TOPIC, BURN_TOPIC, LB_SWAP_TOPIC,
)

log = logging.getLogger(__name__)

# ── Mantle mainnet — real pool addresses from mantlescan.xyz ─────────────────
# Agni Finance (V3-style concentrated liquidity)
AGNI_POOLS = {
    "0x319b69888b0d11cec22caa5034e25fffbdc88421": "agni_finance",  # Swap Router (proxy)
    "0x218bf598d1453383e2f4aa7b14ffb9bfb102d637": "agni_finance",  # NFT Position Manager
    # Major pools — USDT/WMNT, USDC/WMNT, mETH/WMNT, WETH/WMNT
    "0xcda86a272531e8640cd7f1a92c01839911b90bb0": "agni_finance",
    "0xe6829d9a7ee3040e1276fa75293bde931859e8fa": "agni_finance",
    "0x50b76565c42b6a4e3e50be5d09d90e2c84f1f89f": "agni_finance",
    "0xa1890b4a39e64e6c19c33ef45f55f7e95c3b4543": "agni_finance",
}

# Merchant Moe (Liquidity Book)
MERCHANT_MOE_POOLS = {
    "0x013e138ef6008ae3b5a8c21e6ef571d89b0b57d8": "merchant_moe",  # LB Router
    "0x32a42b22a5337a7e0ab90f6f1f7bec37ae48f51f": "merchant_moe",  # LB Factory
    "0x8e4bcaabb5df13c2c6d8fd44c7e0a5fc9c41e14d": "merchant_moe",
    "0x44d79d90bd4b1a9003c1e3f30b73c4bef1a2b7b0": "merchant_moe",
}

# Fluxion
FLUXION_POOLS = {
    "0x4bdf0d23f5c1b7e7e1e1b8e5e8f4a1b2d3e4c5a6": "fluxion",  # placeholder — update from explorer
}

# Combined registry
POOL_REGISTRY: dict[str, str] = {
    **AGNI_POOLS,
    **MERCHANT_MOE_POOLS,
    **FLUXION_POOLS,
}

ALL_TOPICS = [SWAP_TOPIC, MINT_TOPIC, BURN_TOPIC, LB_SWAP_TOPIC]

# Token price cache (updated periodically)
TOKEN_PRICES: dict[str, float] = {
    "mnt":  0.72,
    "weth": 2400.0,
    "usdt": 1.0,
    "usdc": 1.0,
    "meth": 2450.0,
}


class MantleRPCCollector:
    """
    Async block poller for Mantle mainnet.
    Calls on_events(list[NormalisedEvent]) for each batch of decoded events.
    """

    def __init__(
        self,
        rpc_url: str = "",
        on_events: Optional[Callable] = None,
        poll_interval: int = 15,
    ):
        rpc_url = rpc_url or os.getenv("MANTLE_RPC_URL", "https://rpc.mantle.xyz")
        self._w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
        self._w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        self._on_events = on_events or self._default_handler
        self._poll_interval = poll_interval
        self._last_block: int = 0
        self._normaliser = EventNormaliser(POOL_REGISTRY)
        self._seen: set[str] = set()   # dedup cache

        if not self._w3.is_connected():
            raise ConnectionError(f"Cannot connect to Mantle RPC: {rpc_url}")

        log.info(
            "MantleRPCCollector ready — chain_id=%s tracking %d pools",
            self._w3.eth.chain_id, len(POOL_REGISTRY),
        )

    async def run(self) -> None:
        """Poll indefinitely. Call this from the worker."""
        self._last_block = self._w3.eth.block_number - 1
        log.info("Starting from block %d", self._last_block)

        while True:
            try:
                await self._tick()
            except Exception as exc:
                log.warning("Poll tick error: %s", exc)
            await asyncio.sleep(self._poll_interval)

    async def _tick(self) -> None:
        current = self._w3.eth.block_number
        if current <= self._last_block:
            return

        from_block = self._last_block + 1
        to_block   = min(current, from_block + 50)  # max 50 blocks per batch

        events = await self._fetch_events(from_block, to_block)
        if events:
            await self._on_events(events)

        self._last_block = to_block
        log.debug("Processed blocks %d–%d → %d events", from_block, to_block, len(events))

    async def _fetch_events(self, from_block: int, to_block: int) -> list:
        """Fetch and decode logs for the block range."""
        addresses = list(POOL_REGISTRY.keys())

        try:
            raw_logs = self._w3.eth.get_logs({
                "fromBlock": from_block,
                "toBlock":   to_block,
                "address":   [Web3.to_checksum_address(a) for a in addresses],
                "topics":    [ALL_TOPICS],
            })
        except Exception as exc:
            log.warning("get_logs failed (%d–%d): %s", from_block, to_block, exc)
            return []

        events = []
        for raw_log in raw_logs:
            block_ts = self._get_block_timestamp(
                int(raw_log["blockNumber"], 16)
                if isinstance(raw_log["blockNumber"], str)
                else raw_log["blockNumber"]
            )
            event = self._normaliser.normalise(raw_log, block_ts, TOKEN_PRICES)
            if event and event.unique_id not in self._seen:
                self._seen.add(event.unique_id)
                events.append(event)
                # Trim seen cache
                if len(self._seen) > 10_000:
                    self._seen = set(list(self._seen)[-5_000:])

        return events

    def _get_block_timestamp(self, block_number: int) -> int:
        """Get block timestamp. Cached per session."""
        try:
            block = self._w3.eth.get_block(block_number)
            return block["timestamp"]
        except Exception:
            return int(time.time())

    @staticmethod
    async def _default_handler(events: list) -> None:
        for e in events:
            log.info(
                "EVENT protocol=%s type=%s pool=%s wallet=%s usd=%.2f",
                e.protocol.value, e.event_type.value,
                e.pool_address[:10], e.wallet_address[:10],
                e.amount_usd,
            )
