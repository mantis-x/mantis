"""
MantleRPCCollector: polls Mantle RPC for new blocks and decodes
Swap / Mint / Burn events from tracked protocol contracts.

Runs as an async loop. On each new block, fetches logs for all
registered pool addresses and hands raw logs to the appropriate decoder.
"""
from __future__ import annotations
import asyncio
import logging
import os
from typing import Callable

from web3 import Web3

log = logging.getLogger(__name__)

# Tracked protocol contract addresses on Mantle mainnet
PROTOCOL_CONTRACTS: dict[str, list[str]] = {
    "merchant_moe": [
        "0x...",   # MerchantMoe Router
        "0x...",   # LBPair factory
    ],
    "agni_finance": [
        "0x...",   # Agni Pool factory
    ],
    "fluxion": [
        "0x...",   # Fluxion AMM
    ],
}

# Event topic signatures
TOPICS = {
    "Swap":  Web3.keccak(text="Swap(address,address,int256,int256,uint160,uint128,int24)").hex(),
    "Mint":  Web3.keccak(text="Mint(address,address,int24,int24,uint128,uint256,uint256)").hex(),
    "Burn":  Web3.keccak(text="Burn(address,int24,int24,uint128,uint256,uint256)").hex(),
}


class MantleRPCCollector:
    def __init__(self, rpc_url: str, on_events: Callable):
        self._w3 = Web3(Web3.HTTPProvider(rpc_url))
        self._on_events = on_events
        self._last_block: int = 0

    async def run(self, poll_interval: int = 15) -> None:
        """Poll indefinitely. Call on_events(raw_logs) for each new block."""
        log.info("MantleRPCCollector starting, chain_id=%s", self._w3.eth.chain_id)
        self._last_block = self._w3.eth.block_number - 1

        while True:
            try:
                current = self._w3.eth.block_number
                if current > self._last_block:
                    await self._collect_range(self._last_block + 1, current)
                    self._last_block = current
            except Exception as exc:
                log.warning("RPC poll error: %s", exc)
            await asyncio.sleep(poll_interval)

    async def _collect_range(self, from_block: int, to_block: int) -> None:
        all_addresses = [a for addrs in PROTOCOL_CONTRACTS.values() for a in addrs]
        logs = self._w3.eth.get_logs({
            "fromBlock": from_block,
            "toBlock":   to_block,
            "address":   all_addresses,
            "topics":    [list(TOPICS.values())],
        })
        if logs:
            log.debug("Blocks %s–%s: %s raw logs", from_block, to_block, len(logs))
            await self._on_events(logs)
