"""
ChainCollector — generic EVM block poller.

Polls any EVM-compatible chain for new blocks on a configurable interval.
For each new block range, fetches logs from tracked protocol pools and
hands them to the EventNormaliser.

Instantiate one ChainCollector per enabled chain; run them concurrently
with asyncio.gather().
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Optional

from web3 import Web3
try:
    from web3.middleware import ExtraDataToPOAMiddleware
except ImportError:
    from web3.middleware import geth_poa_middleware as ExtraDataToPOAMiddleware

from src.decoders.event_normaliser import (
    EventNormaliser,
    SWAP_TOPIC, UNIV3_SWAP_TOPIC, MINT_TOPIC, BURN_TOPIC, LB_SWAP_TOPIC,
)
from src.chains import ChainConfig

log = logging.getLogger(__name__)

ALL_TOPICS = [SWAP_TOPIC, UNIV3_SWAP_TOPIC, MINT_TOPIC, BURN_TOPIC, LB_SWAP_TOPIC]


class ChainCollector:
    """
    Async block poller for any EVM-compatible chain.
    Calls on_events(list[NormalisedEvent]) for each batch of decoded events.
    """

    def __init__(
        self,
        config: ChainConfig,
        on_events: Optional[Callable] = None,
    ):
        self._config = config
        rpc_url = config.rpc_url()

        self._w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
        if config.poa_middleware:
            self._w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        self._on_events  = on_events or self._default_handler
        self._last_block: int = 0
        self._normaliser = EventNormaliser(config.pool_registry)
        self._seen: set[str] = set()

        if not self._w3.is_connected():
            raise ConnectionError(
                f"Cannot connect to {config.name} RPC: {rpc_url}"
            )

        log.info(
            "ChainCollector[%s] ready — chain_id=%s tracking %d pools",
            config.name, self._w3.eth.chain_id, len(config.pool_registry),
        )

    @property
    def chain_name(self) -> str:
        return self._config.name

    async def run(self) -> None:
        """Poll indefinitely. Designed to run concurrently via asyncio.gather()."""
        self._last_block = self._w3.eth.block_number - 1
        log.info("ChainCollector[%s] starting from block %d", self._config.name, self._last_block)

        while True:
            try:
                await self._tick()
            except Exception as exc:
                log.warning("ChainCollector[%s] poll error: %s", self._config.name, exc)
            await asyncio.sleep(self._config.poll_interval_s)

    async def _tick(self) -> None:
        current = self._w3.eth.block_number
        if current <= self._last_block:
            return

        from_block = self._last_block + 1
        to_block   = min(current, from_block + self._config.max_blocks_per_batch - 1)

        events = await self._fetch_events(from_block, to_block)
        if events:
            await self._on_events(events)

        self._last_block = to_block
        log.debug(
            "ChainCollector[%s] blocks %d–%d → %d events",
            self._config.name, from_block, to_block, len(events),
        )

    async def _fetch_events(self, from_block: int, to_block: int) -> list:
        addresses = list(self._config.pool_registry.keys())
        if not addresses:
            return []

        try:
            raw_logs = self._w3.eth.get_logs({
                "fromBlock": from_block,
                "toBlock":   to_block,
                "address":   [Web3.to_checksum_address(a) for a in addresses],
            })
        except Exception as exc:
            log.warning(
                "ChainCollector[%s] get_logs failed (%d–%d): %s",
                self._config.name, from_block, to_block, exc,
            )
            return []

        events = []
        for raw_log in raw_logs:
            block_num = (
                int(raw_log["blockNumber"], 16)
                if isinstance(raw_log["blockNumber"], str)
                else raw_log["blockNumber"]
            )
            block_ts = self._get_block_timestamp(block_num)
            event = self._normaliser.normalise(
                raw_log, block_ts, self._config.token_prices,
                chain=self._config.name,
            )
            if event and event.unique_id not in self._seen:
                self._seen.add(event.unique_id)
                events.append(event)
                if len(self._seen) > 10_000:
                    self._seen = set(list(self._seen)[-5_000:])

        return events

    def _get_block_timestamp(self, block_number: int) -> int:
        try:
            block = self._w3.eth.get_block(block_number)
            return block["timestamp"]
        except Exception:
            return int(time.time())

    async def _default_handler(self, events: list) -> None:
        for e in events:
            log.info(
                "EVENT chain=%s protocol=%s type=%s pool=%s wallet=%s usd=%.2f",
                e.chain, e.protocol.value, e.event_type.value,
                e.pool_address[:10], e.wallet_address[:10],
                e.amount_usd,
            )
