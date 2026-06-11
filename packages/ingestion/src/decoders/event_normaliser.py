"""
EventNormaliser — routes raw Web3 log dicts to the correct
protocol decoder and returns NormalisedEvent instances.

Usage:
    normaliser = EventNormaliser()
    event = normaliser.normalise(raw_log, block_ts, token_prices)
"""
from __future__ import annotations
import logging
from typing import Optional

from src.models.raw_event import NormalisedEvent

log = logging.getLogger(__name__)


# ── Swap event topic (Uniswap V3 style — used by Agni Finance)
SWAP_TOPIC  = "0x19b47279256b2a23a1665c810c8d55a1758940ee09377d4f8d26497a3577dc83"

# ── Mint event topic (LP deposit)
MINT_TOPIC  = "0x7a53080ba414158be7ec69b987b5fb7d07dee101fe85488f0853ae16239d0bde"

# ── Burn event topic (LP withdrawal)
BURN_TOPIC  = "0x0c396cd989a39f4459b5fa1aed6a9a8dcdbc45908acfd67e028cd568da98982c"

# ── Merchant Moe LB swap topic
LB_SWAP_TOPIC = "0xad7d6f97abf51ce18e17a38f4d70e975be9c0708474987bb3e26ad21bd93ca70"


class EventNormaliser:
    """
    Routes raw logs to per-protocol decoders.
    Each decoder is registered with a set of pool addresses it owns.
    """

    def __init__(self, pool_registry: dict[str, str]):
        """
        pool_registry: { pool_address_lower: protocol_name }
        e.g. { "0x319b6988...": "agni_finance" }
        """
        self._registry = {k.lower(): v for k, v in pool_registry.items()}

    def normalise(
        self,
        raw_log: dict,
        block_timestamp: int,
        token_prices: dict[str, float],
    ) -> Optional[NormalisedEvent]:
        """
        Decode one raw log into a NormalisedEvent.
        Returns None if unrecognised or decoding fails.
        """
        address = raw_log.get("address", "").lower()
        protocol = self._registry.get(address)

        if not protocol:
            return None  # not a tracked pool

        topics = raw_log.get("topics", [])
        if not topics:
            return None

        topic0 = topics[0].hex() if isinstance(topics[0], bytes) else topics[0]

        try:
            if topic0 in (SWAP_TOPIC,):
                return self._decode_v3_swap(
                    raw_log, protocol, address, block_timestamp, token_prices
                )
            elif topic0 == MINT_TOPIC:
                return self._decode_v3_mint(
                    raw_log, protocol, address, block_timestamp, token_prices
                )
            elif topic0 == BURN_TOPIC:
                return self._decode_v3_burn(
                    raw_log, protocol, address, block_timestamp, token_prices
                )
            elif topic0 == LB_SWAP_TOPIC:
                return self._decode_lb_swap(
                    raw_log, protocol, address, block_timestamp, token_prices
                )
        except Exception as exc:
            log.debug("Decode error [%s %s]: %s", protocol, topic0[:10], exc)

        return None

    # ── V3 Swap decoder (Agni Finance / Fluxion) ─────────────────────────────

    def _decode_v3_swap(
        self, log_: dict, protocol: str, pool: str,
        ts: int, prices: dict
    ) -> Optional[NormalisedEvent]:
        from eth_abi import decode
        from src.models.raw_event import EventType, Protocol
        from datetime import datetime, timezone

        data = bytes.fromhex(log_["data"][2:] if log_["data"].startswith("0x") else log_["data"])
        # Swap(address sender, address recipient, int256 amount0, int256 amount1,
        #      uint160 sqrtPriceX96, uint128 liquidity, int24 tick,
        #      uint128 protocolFeesToken0, uint128 protocolFeesToken1)
        decoded = decode(
            ["int256", "int256", "uint160", "uint128", "int24", "uint128", "uint128"],
            data,
        )
        amount0, amount1 = decoded[0], decoded[1]

        # Sender from topic[1]
        topics = log_.get("topics", [])
        sender = "0x" + (topics[1].hex() if isinstance(topics[1], bytes) else topics[1])[-40:]

        # Estimate USD value from raw amounts (simplified — use abs larger leg)
        raw_amount = max(abs(amount0), abs(amount1))
        amount_usd = self._estimate_usd(raw_amount, prices)

        return NormalisedEvent(
            block_number   = int(log_["blockNumber"], 16) if isinstance(log_["blockNumber"], str) else log_["blockNumber"],
            tx_hash        = log_["transactionHash"].hex() if isinstance(log_["transactionHash"], bytes) else log_["transactionHash"],
            log_index      = int(log_["logIndex"], 16) if isinstance(log_["logIndex"], str) else log_["logIndex"],
            protocol       = Protocol(protocol),
            pool_address   = pool,
            wallet_address = sender,
            event_type     = EventType.SWAP,
            amount_usd     = amount_usd,
            amount_in      = int(abs(amount0)),
            amount_out     = int(abs(amount1)),
            timestamp      = datetime.fromtimestamp(ts, tz=timezone.utc),
        )

    def _decode_v3_mint(
        self, log_: dict, protocol: str, pool: str,
        ts: int, prices: dict
    ) -> Optional[NormalisedEvent]:
        from eth_abi import decode
        from src.models.raw_event import EventType, Protocol
        from datetime import datetime, timezone

        data = bytes.fromhex(log_["data"][2:] if log_["data"].startswith("0x") else log_["data"])
        decoded = decode(["address", "uint128", "uint256", "uint256"], data)
        amount0, amount1 = decoded[2], decoded[3]
        amount_usd = self._estimate_usd(max(amount0, amount1), prices)

        topics = log_.get("topics", [])
        owner = "0x" + (topics[1].hex() if isinstance(topics[1], bytes) else topics[1])[-40:]

        return NormalisedEvent(
            block_number   = int(log_["blockNumber"], 16) if isinstance(log_["blockNumber"], str) else log_["blockNumber"],
            tx_hash        = log_["transactionHash"].hex() if isinstance(log_["transactionHash"], bytes) else log_["transactionHash"],
            log_index      = int(log_["logIndex"], 16) if isinstance(log_["logIndex"], str) else log_["logIndex"],
            protocol       = Protocol(protocol),
            pool_address   = pool,
            wallet_address = owner,
            event_type     = EventType.MINT,
            amount_usd     = amount_usd,
            amount_in      = int(amount0 + amount1),
            timestamp      = datetime.fromtimestamp(ts, tz=timezone.utc),
        )

    def _decode_v3_burn(
        self, log_: dict, protocol: str, pool: str,
        ts: int, prices: dict
    ) -> Optional[NormalisedEvent]:
        from eth_abi import decode
        from src.models.raw_event import EventType, Protocol
        from datetime import datetime, timezone

        data = bytes.fromhex(log_["data"][2:] if log_["data"].startswith("0x") else log_["data"])
        decoded = decode(["uint128", "uint256", "uint256"], data)
        amount0, amount1 = decoded[1], decoded[2]
        amount_usd = self._estimate_usd(max(amount0, amount1), prices)

        topics = log_.get("topics", [])
        owner = "0x" + (topics[1].hex() if isinstance(topics[1], bytes) else topics[1])[-40:]

        return NormalisedEvent(
            block_number   = int(log_["blockNumber"], 16) if isinstance(log_["blockNumber"], str) else log_["blockNumber"],
            tx_hash        = log_["transactionHash"].hex() if isinstance(log_["transactionHash"], bytes) else log_["transactionHash"],
            log_index      = int(log_["logIndex"], 16) if isinstance(log_["logIndex"], str) else log_["logIndex"],
            protocol       = Protocol(protocol),
            pool_address   = pool,
            wallet_address = owner,
            event_type     = EventType.BURN,
            amount_usd     = amount_usd,
            amount_out     = int(amount0 + amount1),
            timestamp      = datetime.fromtimestamp(ts, tz=timezone.utc),
        )

    def _decode_lb_swap(
        self, log_: dict, protocol: str, pool: str,
        ts: int, prices: dict
    ) -> Optional[NormalisedEvent]:
        """Merchant Moe Liquidity Book swap."""
        from src.models.raw_event import EventType, Protocol
        from datetime import datetime, timezone

        topics = log_.get("topics", [])
        sender = "0x" + (topics[1].hex() if isinstance(topics[1], bytes) else topics[1])[-40:]

        return NormalisedEvent(
            block_number   = int(log_["blockNumber"], 16) if isinstance(log_["blockNumber"], str) else log_["blockNumber"],
            tx_hash        = log_["transactionHash"].hex() if isinstance(log_["transactionHash"], bytes) else log_["transactionHash"],
            log_index      = int(log_["logIndex"], 16) if isinstance(log_["logIndex"], str) else log_["logIndex"],
            protocol       = Protocol(protocol),
            pool_address   = pool,
            wallet_address = sender,
            event_type     = EventType.SWAP,
            amount_usd     = 0.0,   # enriched by price oracle later
            timestamp      = datetime.fromtimestamp(ts, tz=timezone.utc),
        )

    @staticmethod
    def _estimate_usd(raw_amount: int, prices: dict) -> float:
        """
        Rough USD estimate from raw token amount.
        Uses MNT price as proxy if available; falls back to 0.
        Proper pricing requires token address lookup — done in enricher.
        """
        mnt_price = prices.get("mnt", 0.72)   # fallback ~$0.72
        # Most Mantle pools use 18 decimals
        return round((raw_amount / 1e18) * mnt_price, 4)
