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

# GMX V1 Vault events — all params non-indexed, decoded entirely from data
# keccak256("Swap(address,address,address,uint256,uint256,uint256,uint256)")
GMX_SWAP_TOPIC             = "0x0874b2d545cb271cdbda4e093020c452328b24af12382ed62c4d00f5c26709db"
# keccak256("IncreasePosition(bytes32,address,address,address,uint256,uint256,bool,uint256,uint256)")
GMX_INCREASE_POSITION_TOPIC = "0x2fe68525253654c21998f35787a8d0f361905ef647c854092430ab65f2f15022"
# keccak256("DecreasePosition(bytes32,address,address,address,uint256,uint256,bool,uint256,uint256)")
GMX_DECREASE_POSITION_TOPIC = "0x93d75d64d1f84fc6f430a64fc578bdd4c1e090e90ea2d51773e626d19de56d30"

# GMX V1 stores sizeDelta and collateralDelta in USD × 10^30
_GMX_USD_PRECISION = 10 ** 30

# Standard ERC-20 Transfer — keccak256("Transfer(address,address,uint256)")
# Used for HashKey Chain flow monitoring: no DEX with meaningful swap volume
# was found at launch, so large token movements between wallets are the
# signal source instead (see Protocol.HASHKEY_FLOWS).
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


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
            elif topic0 == GMX_SWAP_TOPIC:
                return self._decode_gmx_swap(
                    raw_log, pool_meta, address, block_timestamp, token_prices, chain
                )
            elif topic0 == GMX_INCREASE_POSITION_TOPIC:
                return self._decode_gmx_position(
                    raw_log, pool_meta, address, block_timestamp, chain, is_increase=True
                )
            elif topic0 == GMX_DECREASE_POSITION_TOPIC:
                return self._decode_gmx_position(
                    raw_log, pool_meta, address, block_timestamp, chain, is_increase=False
                )
            elif topic0 == TRANSFER_TOPIC:
                return self._decode_token_transfer(
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

        data = _log_data(log_)
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

        data = _log_data(log_)
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

        data = _log_data(log_)
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

        data = _log_data(log_)
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

    # ── GMX V1 spot swap ──────────────────────────────────────────────────────

    def _decode_gmx_swap(
        self, log_: dict, pool_meta: PoolMeta, pool: str,
        ts: int, prices: dict, chain: str = "arbitrum",
    ) -> Optional[NormalisedEvent]:
        """
        GMX V1 Vault Swap — all 7 params are non-indexed (in data, not topics).
        Signature: Swap(address account, address tokenIn, address tokenOut,
                        uint256 amountIn, uint256 amountOut,
                        uint256 amountOutAfterFees, uint256 feeBasisPoints)
        USD value: derived from amountIn using known token prices if available,
                   else amountOutAfterFees treated as USD proxy (GMX uses USD oracle).
        """
        from eth_abi import decode
        from src.models.raw_event import EventType, Protocol

        data = _log_data(log_)
        decoded = decode(
            ["address", "address", "address", "uint256", "uint256", "uint256", "uint256"],
            data,
        )
        account, token_in, token_out, amount_in, amount_out, amount_out_after_fees, _ = decoded

        # Estimate USD: prefer using token_in price from registry; fall back to stablecoin heuristic
        amount_usd = _estimate_gmx_usd(token_in.lower(), amount_in, prices)

        return NormalisedEvent(
            block_number   = _block_num(log_),
            tx_hash        = _tx_hash(log_),
            log_index      = _log_index(log_),
            chain          = chain,
            protocol       = Protocol(pool_meta.protocol),
            pool_address   = pool,
            wallet_address = account.lower(),
            event_type     = EventType.SWAP,
            amount_usd     = amount_usd,
            token_in       = token_in.lower(),
            token_out      = token_out.lower(),
            amount_in      = int(amount_in),
            amount_out     = int(amount_out_after_fees),
            timestamp      = _ts(ts),
        )

    # ── GMX V1 perpetual position (increase / decrease) ───────────────────────

    def _decode_gmx_position(
        self, log_: dict, pool_meta: PoolMeta, pool: str,
        ts: int, chain: str = "arbitrum", *, is_increase: bool,
    ) -> Optional[NormalisedEvent]:
        """
        GMX V1 IncreasePosition / DecreasePosition — all 9 params non-indexed.
        Signature: (bytes32 key, address account, address collateralToken,
                    address indexToken, uint256 collateralDelta, uint256 sizeDelta,
                    bool isLong, uint256 price, uint256 fee)
        sizeDelta is in USD × 10^30 (GMX V1 USD precision).
        """
        from eth_abi import decode
        from src.models.raw_event import EventType, Protocol

        data = _log_data(log_)
        decoded = decode(
            ["bytes32", "address", "address", "address",
             "uint256", "uint256", "bool", "uint256", "uint256"],
            data,
        )
        _, account, collateral_token, index_token, _, size_delta, is_long, price, _ = decoded

        amount_usd = round(size_delta / _GMX_USD_PRECISION, 4)
        event_type = EventType.OPEN_POSITION if is_increase else EventType.CLOSE_POSITION

        return NormalisedEvent(
            block_number   = _block_num(log_),
            tx_hash        = _tx_hash(log_),
            log_index      = _log_index(log_),
            chain          = chain,
            protocol       = Protocol(pool_meta.protocol),
            pool_address   = pool,
            wallet_address = account.lower(),
            event_type     = event_type,
            amount_usd     = amount_usd,
            token_in       = collateral_token.lower(),
            token_out      = index_token.lower(),
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

    # ── ERC-20 Transfer (HashKey Chain flow monitoring) ───────────────────────

    def _decode_token_transfer(
        self, log_: dict, pool_meta: PoolMeta, pool: str,
        ts: int, prices: dict, chain: str = "hashkey",
    ) -> Optional[NormalisedEvent]:
        """
        Standard ERC-20 Transfer(address indexed from, address indexed to,
        uint256 value). from/to are indexed (topics[1]/topics[2]); value is
        the sole (non-indexed) data field.

        wallet_address is the sender (from) — the party moving funds out,
        consistent with every other decoder in this file tracking a single
        primary actor. The recipient (to) isn't captured: NormalisedEvent's
        token_in/token_out fields are for token contract addresses, and
        reusing them for a wallet address here would be misleading to
        anyone reading a decoded event later — not worth it for a field
        that's dropped before reaching the Redis queue anyway (see
        ingestion/src/worker.py's payload, which doesn't forward token_in/
        token_out). pool_meta.token0 carries the monitored token's
        decimals/price_key (there's no "pair" here — pool_address is just
        the token contract itself).
        """
        from eth_abi import decode
        from src.models.raw_event import EventType, Protocol

        topics = log_.get("topics", [])
        if len(topics) < 3:
            return None

        sender = "0x" + (topics[1].hex() if isinstance(topics[1], bytes) else topics[1])[-40:]

        data = _log_data(log_)
        (value,) = decode(["uint256"], data)

        amount_usd = 0.0
        if pool_meta.token0:
            amount_usd = round(
                value / (10 ** pool_meta.token0.decimals) * prices.get(pool_meta.token0.price_key, 0.0),
                4,
            )

        return NormalisedEvent(
            block_number   = _block_num(log_),
            tx_hash        = _tx_hash(log_),
            log_index      = _log_index(log_),
            chain          = chain,
            protocol       = Protocol(pool_meta.protocol),
            pool_address   = pool,
            wallet_address = sender,
            event_type     = EventType.TRANSFER,
            amount_usd     = amount_usd,
            amount_in      = int(value),
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


def _estimate_gmx_usd(token_addr: str, amount_in: int, prices: dict[str, float]) -> float:
    """
    Estimate USD value of a GMX V1 Swap given token address and raw amount.
    Maps known Arbitrum token addresses to price keys. Falls back to 0 for unknowns.
    """
    _GMX_TOKEN_MAP = {
        # WETH
        "0x82af49447d8a07e3bd95bd0d56f35241523fbab1": ("eth",  18),
        # native USDC (Circle)
        "0xaf88d065e77c8cc2239327c5edb3a432268e5831": ("usdc",  6),
        # USDC.e (bridged)
        "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8": ("usdc",  6),
        # USDT
        "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9": ("usdt",  6),
        # WBTC
        "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f": ("wbtc",  8),
        # ARB
        "0x912ce59144191c1204e64559fe8253a0e49e6548": ("arb",  18),
    }
    entry = _GMX_TOKEN_MAP.get(token_addr)
    if not entry:
        return 0.0
    price_key, decimals = entry
    price = prices.get(price_key, 0.0)
    return round((amount_in / (10 ** decimals)) * price, 4)


# ── Log field helpers ─────────────────────────────────────────────────────────

def _log_data(log_: dict) -> bytes:
    """
    Raw ABI-encoded bytes from a log's "data" field.

    Real web3.py responses (w3.eth.get_logs()) return this as HexBytes, a
    bytes subclass — .startswith("0x") on it raises TypeError (bytes
    .startswith rejects a str argument), which every decoder in this file
    used to do unconditionally. That exception was silently swallowed by
    normalise()'s outer try/except, so no decoder ever actually parsed a
    real on-chain log; only the plain-string "data" values built by unit
    tests worked. Handle both shapes explicitly instead of assuming str.
    """
    raw = log_["data"]
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    return bytes.fromhex(raw[2:] if raw.startswith("0x") else raw)


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
