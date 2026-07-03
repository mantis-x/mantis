"""
EventNormaliser — routes raw Web3 log dicts to the correct
protocol decoder and returns NormalisedEvent instances.

Supported swap event variants:
  SWAP_TOPIC       — Agni Finance (Mantle) 9-field variant with protocol fees
                     sender indexed in topics[1]
                     data: (int256,int256,uint160,uint128,int24,uint128,uint128)
  UNIV3_SWAP_TOPIC — canonical Uniswap V3 used on Arbitrum
                     sender in topics[1], recipient in topics[2]
                     data: (int256,int256,uint160,uint128,int24)
  LB_SWAP_TOPIC    — Merchant Moe / Trader Joe Liquidity Book swap

Mint and Burn event signatures are identical across all V3 forks.
"""
from __future__ import annotations
import logging
from typing import Optional

from src.chains import PoolMeta
from src.models.raw_event import NormalisedEvent

log = logging.getLogger(__name__)


# ── Event topic hashes ────────────────────────────────────────────────────────

# Agni Finance Swap (Mantle): 9-field data including protocol fees
SWAP_TOPIC  = "0x19b47279256b2a23a1665c810c8d55a1758940ee09377d4f8d26497a3577dc83"

# Canonical Uniswap V3 Swap: keccak256("Swap(address,address,int256,int256,uint160,uint128,int24)")
UNIV3_SWAP_TOPIC = "0xc42079f94fa31298d6da5b7c9a54f2a7af6e8b8cf43f49c74b49cd897f14c4d9"

# V3 LP events — same signature across all V3 forks (Agni, Uniswap V3, etc.)
MINT_TOPIC  = "0x7a53080ba414158be7ec69b987b5fb7d07dee101fe85488f0853ae16239d0bde"
BURN_TOPIC  = "0x0c396cd989a39f4459b5fa1aed6a9a8dcdbc45908acfd67e028cd568da98982c"

# Liquidity Book swap — Merchant Moe (Mantle) and Trader Joe (Arbitrum) are the same fork
LB_SWAP_TOPIC = "0xad7d6f97abf51ce18e17a38f4d70e975be9c0708474987bb3e26ad21bd93ca70"


class EventNormaliser:
    """
    Routes raw logs to per-protocol decoders.
    pool_registry maps lowercase address → PoolMeta.
    """

    def __init__(self, pool_registry: dict):
        """
        pool_registry: { pool_address_lower: PoolMeta }
        """
        self._registry: dict[str, PoolMeta] = {
            k.lower(): (v if isinstance(v, PoolMeta) else PoolMeta(v))
            for k, v in pool_registry.items()
        }

    def normalise(
        self,
        raw_log: dict,
        block_timestamp: int,
        token_prices: dict[str, float],
        chain: str = "mantle",
    ) -> Optional[NormalisedEvent]:
        """
        Decode one raw log into a NormalisedEvent.
        Returns None if unrecognised or decoding fails.
        """
        address = raw_log.get("address", "").lower()
        pool_meta = self._registry.get(address)

        if not pool_meta:
            return None

        topics = raw_log.get("topics", [])
        if not topics:
            return None

        topic0 = topics[0].hex() if isinstance(topics[0], bytes) else topics[0]

        try:
            if topic0 == SWAP_TOPIC:
                return self._decode_agni_swap(
                    raw_log, pool_meta, address, block_timestamp, token_prices, chain
                )
            elif topic0 == UNIV3_SWAP_TOPIC:
                return self._decode_univ3_swap(
                    raw_log, pool_meta, address, block_timestamp, token_prices, chain
                )
            elif topic0 == MINT_TOPIC:
                return self._decode_v3_mint(
                    raw_log, pool_meta, address, block_timestamp, token_prices, chain
                )
            elif topic0 == BURN_TOPIC:
                return self._decode_v3_burn(
                    raw_log, pool_meta, address, block_timestamp, token_prices, chain
                )
            elif topic0 == LB_SWAP_TOPIC:
                return self._decode_lb_swap(
                    raw_log, pool_meta, address, block_timestamp, token_prices, chain
                )
        except Exception as exc:
            log.debug("Decode error [%s %s]: %s", pool_meta.protocol, topic0[:10], exc)

        return None

    # ── Agni Finance swap (Mantle-specific 9-field variant) ───────────────────

    def _decode_agni_swap(
        self, log_: dict, pool_meta: PoolMeta, pool: str,
        ts: int, prices: dict, chain: str = "mantle",
    ) -> Optional[NormalisedEvent]:
        from eth_abi import decode
        from src.models.raw_event import EventType, Protocol
        from datetime import datetime, timezone

        data = bytes.fromhex(log_["data"][2:] if log_["data"].startswith("0x") else log_["data"])
        # Agni Swap(sender, recipient, int256 amount0, int256 amount1,
        #           uint160 sqrtPriceX96, uint128 liquidity, int24 tick,
        #           uint128 protocolFeesToken0, uint128 protocolFeesToken1)
        # sender and recipient are indexed (topics[1], topics[2])
        # data contains the remaining 7 fields
        decoded = decode(
            ["int256", "int256", "uint160", "uint128", "int24", "uint128", "uint128"],
            data,
        )
        amount0, amount1 = decoded[0], decoded[1]

        topics = log_.get("topics", [])
        sender = "0x" + (topics[1].hex() if isinstance(topics[1], bytes) else topics[1])[-40:]

        amount_usd = _estimate_usd(amount0, amount1, pool_meta, prices)

        return NormalisedEvent(
            block_number   = _block_num(log_),
            tx_hash        = _tx_hash(log_),
            log_index      = _log_index(log_),
            chain          = chain,
            protocol       = Protocol(pool_meta.protocol),
            pool_address   = pool,
            wallet_address = sender,
            event_type     = EventType.SWAP,
            amount_usd     = amount_usd,
            amount_in      = int(abs(amount0)),
            amount_out     = int(abs(amount1)),
            timestamp      = _ts(ts),
        )

    # ── Canonical Uniswap V3 swap (Arbitrum and other chains) ────────────────

    def _decode_univ3_swap(
        self, log_: dict, pool_meta: PoolMeta, pool: str,
        ts: int, prices: dict, chain: str = "mantle",
    ) -> Optional[NormalisedEvent]:
        from eth_abi import decode
        from src.models.raw_event import EventType, Protocol
        from datetime import datetime, timezone

        data = bytes.fromhex(log_["data"][2:] if log_["data"].startswith("0x") else log_["data"])
        # Canonical Uniswap V3 Swap(address sender, address recipient,
        #                           int256 amount0, int256 amount1,
        #                           uint160 sqrtPriceX96, uint128 liquidity, int24 tick)
        # sender = topics[1], recipient = topics[2]
        # data: 5 fields (no protocol fees)
        decoded = decode(
            ["int256", "int256", "uint160", "uint128", "int24"],
            data,
        )
        amount0, amount1 = decoded[0], decoded[1]

        topics = log_.get("topics", [])
        sender = "0x" + (topics[1].hex() if isinstance(topics[1], bytes) else topics[1])[-40:]

        amount_usd = _estimate_usd(amount0, amount1, pool_meta, prices)

        return NormalisedEvent(
            block_number   = _block_num(log_),
            tx_hash        = _tx_hash(log_),
            log_index      = _log_index(log_),
            chain          = chain,
            protocol       = Protocol(pool_meta.protocol),
            pool_address   = pool,
            wallet_address = sender,
            event_type     = EventType.SWAP,
            amount_usd     = amount_usd,
            amount_in      = int(abs(amount0)),
            amount_out     = int(abs(amount1)),
            timestamp      = _ts(ts),
        )

    # ── V3 LP events (shared across all V3 forks) ────────────────────────────

    def _decode_v3_mint(
        self, log_: dict, pool_meta: PoolMeta, pool: str,
        ts: int, prices: dict, chain: str = "mantle",
    ) -> Optional[NormalisedEvent]:
        from eth_abi import decode
        from src.models.raw_event import EventType, Protocol

        data = bytes.fromhex(log_["data"][2:] if log_["data"].startswith("0x") else log_["data"])
        decoded = decode(["address", "uint128", "uint256", "uint256"], data)
        amount0, amount1 = decoded[2], decoded[3]
        amount_usd = _estimate_usd(amount0, amount1, pool_meta, prices)

        topics = log_.get("topics", [])
        owner = "0x" + (topics[1].hex() if isinstance(topics[1], bytes) else topics[1])[-40:]

        return NormalisedEvent(
            block_number   = _block_num(log_),
            tx_hash        = _tx_hash(log_),
            log_index      = _log_index(log_),
            chain          = chain,
            protocol       = Protocol(pool_meta.protocol),
            pool_address   = pool,
            wallet_address = owner,
            event_type     = EventType.MINT,
            amount_usd     = amount_usd,
            amount_in      = int(amount0 + amount1),
            timestamp      = _ts(ts),
        )

    def _decode_v3_burn(
        self, log_: dict, pool_meta: PoolMeta, pool: str,
        ts: int, prices: dict, chain: str = "mantle",
    ) -> Optional[NormalisedEvent]:
        from eth_abi import decode
        from src.models.raw_event import EventType, Protocol

        data = bytes.fromhex(log_["data"][2:] if log_["data"].startswith("0x") else log_["data"])
        decoded = decode(["uint128", "uint256", "uint256"], data)
        amount0, amount1 = decoded[1], decoded[2]
        amount_usd = _estimate_usd(amount0, amount1, pool_meta, prices)

        topics = log_.get("topics", [])
        owner = "0x" + (topics[1].hex() if isinstance(topics[1], bytes) else topics[1])[-40:]

        return NormalisedEvent(
            block_number   = _block_num(log_),
            tx_hash        = _tx_hash(log_),
            log_index      = _log_index(log_),
            chain          = chain,
            protocol       = Protocol(pool_meta.protocol),
            pool_address   = pool,
            wallet_address = owner,
            event_type     = EventType.BURN,
            amount_usd     = amount_usd,
            amount_out     = int(amount0 + amount1),
            timestamp      = _ts(ts),
        )

    # ── Liquidity Book swap (Merchant Moe / Trader Joe) ──────────────────────

    def _decode_lb_swap(
        self, log_: dict, pool_meta: PoolMeta, pool: str,
        ts: int, prices: dict, chain: str = "mantle",
    ) -> Optional[NormalisedEvent]:
        from src.models.raw_event import EventType, Protocol

        topics = log_.get("topics", [])
        sender = "0x" + (topics[1].hex() if isinstance(topics[1], bytes) else topics[1])[-40:]

        return NormalisedEvent(
            block_number   = _block_num(log_),
            tx_hash        = _tx_hash(log_),
            log_index      = _log_index(log_),
            chain          = chain,
            protocol       = Protocol(pool_meta.protocol),
            pool_address   = pool,
            wallet_address = sender,
            event_type     = EventType.SWAP,
            amount_usd     = 0.0,   # enriched by price oracle later
            timestamp      = _ts(ts),
        )


# ── USD estimation ─────────────────────────────────────────────────────────────

def _estimate_usd(
    amount0: int,
    amount1: int,
    pool_meta: PoolMeta,
    prices: dict[str, float],
) -> float:
    """
    Estimate USD value of a swap/LP event.

    Token-aware mode (pool_meta.token0 and token1 are set):
      Prices each leg using its token's decimals and price key from the chain
      config's token_prices dict. Returns the larger of the two USD values.

    Legacy fallback (token metadata absent — Mantle pools without metadata):
      Uses the larger raw amount, assumes 18 decimals, prices as MNT.
    """
    if pool_meta.token0 and pool_meta.token1:
        t0 = pool_meta.token0
        t1 = pool_meta.token1
        usd0 = abs(amount0) / (10 ** t0.decimals) * prices.get(t0.price_key, 0.0)
        usd1 = abs(amount1) / (10 ** t1.decimals) * prices.get(t1.price_key, 0.0)
        return round(max(usd0, usd1), 4)

    # Legacy: assume 18 decimals, price as native MNT
    mnt_price = prices.get("mnt", 0.72)
    raw = max(abs(amount0), abs(amount1))
    return round((raw / 1e18) * mnt_price, 4)


# ── Log field helpers ─────────────────────────────────────────────────────────

def _block_num(log_: dict) -> int:
    v = log_["blockNumber"]
    return int(v, 16) if isinstance(v, str) else int(v)

def _tx_hash(log_: dict) -> str:
    v = log_["transactionHash"]
    return v.hex() if isinstance(v, bytes) else v

def _log_index(log_: dict) -> int:
    v = log_["logIndex"]
    return int(v, 16) if isinstance(v, str) else int(v)

def _ts(ts: int):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc)
