"""
Chain registry — single source of truth for EVM chain configs used by ingestion.

Add a chain entry here and set CHAINS=mantle,<new> to enable it at startup.
Default: CHAINS=mantle
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# ── Token / pool metadata ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class TokenMeta:
    address:   str   # lowercase
    price_key: str   # key into ChainConfig.token_prices
    decimals:  int


@dataclass(frozen=True)
class PoolMeta:
    """
    Per-pool metadata consumed by the event normaliser.
    token0/token1 are None for Mantle pools where we use legacy USD estimation.
    Set both to enable token-aware USD pricing (required for Arbitrum).
    """
    protocol: str
    token0:   Optional[TokenMeta] = None   # token with lower address (Uniswap V3 ordering)
    token1:   Optional[TokenMeta] = None


# ── Chain config ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ChainConfig:
    name:                 str
    chain_id:             int
    rpc_env:              str
    default_rpc:          str
    explorer_base:        str
    poa_middleware:       bool
    poll_interval_s:      int
    max_blocks_per_batch: int
    native_token:         str
    pool_registry: dict = field(default_factory=dict, compare=False, hash=False)
    token_prices:  dict = field(default_factory=dict, compare=False, hash=False)
    # price_key -> Chainlink AggregatorV3 feed address. Live-read at ingest
    # time and overlaid onto token_prices; keys with no feed configured (or
    # whose feed read fails) fall back to the static token_prices value.
    # Only populate an entry here once the feed address is verified on-chain
    # (get_code + description()) — do not add addresses from memory.
    price_feeds:   dict = field(default_factory=dict, compare=False, hash=False)

    def rpc_url(self) -> str:
        return os.getenv(self.rpc_env, self.default_rpc)

    def explorer_tx(self, tx_hash: str) -> str:
        return f"{self.explorer_base}/tx/{tx_hash}"

    def explorer_address(self, address: str) -> str:
        return f"{self.explorer_base}/address/{address}"


# ── Mantle ────────────────────────────────────────────────────────────────────
#
# Token addresses below verified on-chain 2026-07-16 via name()/symbol()/
# decimals() calls against rpc.mantle.xyz, cross-checked against
# swapsicledex/swapsicle-default-token-list's tokens/mantle.json.
_MANTLE_WMNT = TokenMeta("0x78c1b0c915c4faa5fffa6cabf0219da63d7f4cb8", "mnt",  18)
_MANTLE_USDT = TokenMeta("0x201eba5cc46d216ce6dc03f6a759e8e766e956ae", "usdt",  6)
_MANTLE_USDC = TokenMeta("0x09bc4e0d864854c6afb6eb9a9cdf58ac190d0df9", "usdc",  6)
_MANTLE_WETH = TokenMeta("0xdeaddeaddeaddeaddeaddeaddeaddeaddead1111", "weth", 18)

MANTLE = ChainConfig(
    name="mantle",
    chain_id=5000,
    rpc_env="MANTLE_RPC_URL",
    default_rpc="https://rpc.mantle.xyz",
    explorer_base="https://explorer.mantle.xyz",
    poa_middleware=True,
    poll_interval_s=15,
    max_blocks_per_batch=50,
    native_token="mnt",
    pool_registry={
        # Agni Finance (PancakeV3-fork concentrated liquidity on Mantle).
        # Swap topic: Agni's 9-field variant (includes protocol fees).
        #
        # 2026-07-16: the previous entries here were never real pool
        # contracts -- eth_getLogs for the Swap topic returned zero hits,
        # ever, on all of them. Two of the five ("USDT/WMNT", "USDC/WMNT")
        # turned out to be the mETH and cmETH *token* contracts (confirmed
        # via name()/symbol() -- name='mETH'/'cmETH'), not pools; the
        # other three either weren't valid ERC-20s or (the "Swap Router")
        # was a real router address that structurally can't emit pool-level
        # Swap events. Every real Mantle event this project has ever
        # ingested was a stray ERC-20 Transfer/Approval from those token
        # contracts, priced at $0 by the transfer decoder -- not decoded
        # DEX activity.
        #
        # Replaced with the three most active real pools, found via
        # AgniFactory.getPool() (0x25780dc8Fc3cfBD75F33bFDAB65e969b603b2035,
        # from Agni's own Immunefi bug-bounty scope) across all fee tiers,
        # then verified by real eth_getLogs Swap-topic hit counts on-chain
        # (20,000-block sample, ~11h): WMNT/USDC 40 swaps, WMNT/WETH 27,
        # WMNT/USDT 11. token0/token1 set (both directly confirmed via each
        # pool's own token0()/token1() view functions) so pricing uses the
        # token-aware branch of _estimate_usd, not the legacy 18-decimal-MNT
        # fallback that silently mis-prices any 6-decimal leg.
        "0x1858d52cf57c07a018171d7a1e68dc081f17144f": PoolMeta(
            "agni_finance", token0=_MANTLE_USDC, token1=_MANTLE_WMNT,
        ),  # WMNT/USDC 0.05% -- most active of the three, ~$35/hr real volume
        "0x54169896d28dec0ffabe3b16f90f71323774949f": PoolMeta(
            "agni_finance", token0=_MANTLE_WMNT, token1=_MANTLE_WETH,
        ),  # WMNT/WETH 0.05%
        "0xd08c50f7e69e9aeb2867deff4a8053d9a855e26a": PoolMeta(
            "agni_finance", token0=_MANTLE_USDT, token1=_MANTLE_WMNT,
        ),  # WMNT/USDT 0.05% -- quietest of the three, ~1 swap/hr
        # Merchant Moe (Trader Joe Liquidity Book fork) -- verified 2026-07-19.
        # The previous 4 addresses here were pure fakes: eth_getCode returned
        # "0x" (no contract at all, not even the wrong kind) for every one of
        # them, including two labeled "LB Router"/"LB Factory" -- structurally
        # wrong anyway, since a router/factory can't emit pool-level Swap
        # events, same mistake as the original Agni entries. Real addresses
        # (LBFactory 0xa6630671775c4EA2743840F9A5016dCf2A104054, LBRouter
        # 0x013e138EF6008ae5FDFDE29700e3f2Bc61d21E3a) found via Merchant Moe's
        # own docs (docs.merchantmoe.com/resources/contracts) -- note the fake
        # router address shared a prefix with this real one before diverging,
        # suggesting a corrupted copy rather than a pure invention. Queried
        # LBFactory.getAllLBPairs() for WMNT/USDC, WMNT/WETH, WMNT/USDT across
        # every bin-step tier (16 candidate pairs total), then checked each
        # for real Swap-topic activity via eth_getLogs (20,000-block sample,
        # ~11h) -- only ONE had any: WMNT/WETH at 10bp, 50 real swaps. The
        # other 15 candidate pairs had zero activity and are not included.
        #
        # NOTE: _decode_lb_swap() in event_normaliser.py currently hardcodes
        # amount_usd=0.0 for every LB swap -- it never actually decodes the
        # packed amountsIn/amountsOut bytes32 fields the way the Uniswap V3
        # decoder does for amount0/amount1. This pool will ingest as real
        # $0 swaps until that decoder gap is closed separately; flagged in
        # PROJECT_STATE.md rather than rushed here, since LB's bin-based
        # accounting needs its own careful implementation.
        "0x1606c79be3ebd70d8d40bac6287e23005cfbefa2": PoolMeta(
            "merchant_moe", token0=_MANTLE_WMNT, token1=_MANTLE_WETH,
        ),  # WMNT/WETH 10bp -- 50 real swaps/20000 blocks, tokenX/tokenY confirmed via getTokenX()/getTokenY()
        # Fluxion -- removed 2026-07-19. The previous address had no contract
        # code deployed at all (same as the Merchant Moe fakes above). Fluxion
        # is a real, live protocol on Mantle (launched Dec 2025, V2/V3 AMM +
        # orderbook) but finding its real factory/pools AND writing a decoder
        # for its event format (event_normaliser.py has zero Fluxion-specific
        # decoding today) is a bigger lift than a pool-address swap -- left
        # out entirely rather than guess. See PROJECT_STATE.md Next Priorities.
    },
    token_prices={
        "mnt":  0.72,
        "weth": 2400.0,
        "usdt": 1.0,
        "usdc": 1.0,
        "meth": 2450.0,
    },
    # No price_feeds entry: Chainlink has no verified official deployment on
    # Mantle at time of writing. Signals here use the static prices above
    # until a verified feed (or another oracle) is added.
)


# ── Arbitrum ──────────────────────────────────────────────────────────────────
#
# ⚠️  VERIFY all pool addresses on arbiscan.io before enabling in production.
#    Token addresses (WETH, USDC, USDT, WBTC, ARB) are canonical Arbitrum One
#    deployments and are high-confidence.  Pool addresses are derived from
#    Uniswap V3's CREATE2 factory and are consistent with published data, but
#    should be spot-checked against arbiscan before going live.
#
# Uniswap V3 factory (same address cross-chain):
#    0x1F98431c8aD98523631AE4a59f267346ea31F984
# SwapRouter02:   0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45
# NonfungiblePositionManager: 0xC36442b4a4522E871399CD717aBDD847Ab11FE88
#
# Token ordering in pools follows Uniswap V3 convention (lower address = token0).

_ARB_WETH  = TokenMeta("0x82af49447d8a07e3bd95bd0d56f35241523fbab1", "eth",  18)
_ARB_USDC  = TokenMeta("0xaf88d065e77c8cc2239327c5edb3a432268e5831", "usdc",  6)  # native USDC (Circle)
_ARB_USDCe = TokenMeta("0xff970a61a04b1ca14834a43f5de4533ebddb5cc8", "usdc",  6)  # USDC.e (bridged)
_ARB_USDT  = TokenMeta("0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9", "usdt",  6)
_ARB_WBTC  = TokenMeta("0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f", "wbtc",  8)
_ARB_ARB   = TokenMeta("0x912ce59144191c1204e64559fe8253a0e49e6548", "arb",  18)

ARBITRUM = ChainConfig(
    name="arbitrum",
    chain_id=42161,
    rpc_env="ARBITRUM_RPC_URL",
    default_rpc="https://arb1.arbitrum.io/rpc",
    explorer_base="https://arbiscan.io",
    poa_middleware=False,
    poll_interval_s=10,
    max_blocks_per_batch=500,
    native_token="eth",
    pool_registry={
        # Uniswap V3 — canonical Swap topic (5-field data); verify addresses on arbiscan.io
        # WETH (0x82aF) < USDC.e (0xFF97) → token0=WETH, token1=USDC.e
        "0xc6962004f452be9203591991d15f6b388e09e8d0": PoolMeta(
            "uniswap_v3", token0=_ARB_WETH, token1=_ARB_USDCe,   # WETH/USDC.e 0.05%
        ),
        # WETH (0x82aF) < USDC native (0xaf88) → token0=WETH, token1=USDC
        "0xc473e2aee3441bf9240be85eb122abb059a3b57c": PoolMeta(
            "uniswap_v3", token0=_ARB_WETH, token1=_ARB_USDC,    # WETH/USDC 0.05%
        ),
        # WETH (0x82aF) < USDT (0xFd08) → token0=WETH, token1=USDT
        "0x641c00a822e8b671738d32a431a4fb6074e5c79d": PoolMeta(
            "uniswap_v3", token0=_ARB_WETH, token1=_ARB_USDT,    # WETH/USDT 0.05%
        ),
        # WBTC (0x2f2a) < WETH (0x82aF) → token0=WBTC, token1=WETH
        "0x2f5e87c9312fa29aed5c179e456625d79015299c": PoolMeta(
            "uniswap_v3", token0=_ARB_WBTC, token1=_ARB_WETH,    # WBTC/WETH 0.3%
        ),
        # ARB (0x912c) < WETH (0x82aF)?  0x91 > 0x82 → token0=WETH, token1=ARB
        "0xc6f780497a95e246eb9449f5e4770916dcd6396a": PoolMeta(
            "uniswap_v3", token0=_ARB_WETH, token1=_ARB_ARB,     # WETH/ARB 0.3%
        ),
        # WETH (0x82aF) < USDC.e (0xFF97) → WETH/USDC.e 0.3% fee (higher liquidity on some pairs)
        "0x17c14d2c404d167802b16c450d3c99f88f2c4f4d": PoolMeta(
            "uniswap_v3", token0=_ARB_WETH, token1=_ARB_USDCe,   # WETH/USDC.e 0.3%
        ),
        # Trader Joe (Liquidity Book) — same LB_SWAP_TOPIC as Merchant Moe
        # Verify router + pair addresses on arbiscan.io
        "0xb4315e873dbcf96ffd0acd8ea43f689d8c20fB30": PoolMeta("trader_joe"),  # LB Router v2.2
        # GMX V1 — perpetuals + spot swap; all events non-indexed, decoded from data
        # Swap/IncreasePosition/DecreasePosition emitted by Vault
        "0x489ee077994b6658eafa855c308275ead8097c4e": PoolMeta("gmx"),  # GMX V1 Vault
        "0x321f653eed006ad1c29d174e17d96351bde22649": PoolMeta("gmx"),  # GLP Manager
    },
    token_prices={
        # Static fallback only — overridden at runtime by price_feeds below
        # wherever a Chainlink feed is configured and reachable.
        "eth":  2400.0,
        "wbtc": 65000.0,
        "arb":  0.80,
        "usdt": 1.0,
        "usdc": 1.0,
    },
    price_feeds={
        # Chainlink AggregatorV3 feeds on Arbitrum One — each verified via
        # get_code() + description() before being added here.
        "eth":  "0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612",  # ETH / USD
        "wbtc": "0x6ce185860a4963106506C203335A2910413708e9",  # BTC / USD
        "arb":  "0xb2A824043730FE05F3DA2efaFa1CBbe83fa548D6",  # ARB / USD
        "usdc": "0x50834F3163758fcC1Df9973b6e91f0F0F0434aD3",  # USDC / USD
        "usdt": "0x3f3f5dF88dC9F13eac63DF89EC16ef6e7E25DdE7",  # USDT / USD
    },
)


# ── HashKey Chain ────────────────────────────────────────────────────────────
#
# Verified live 2026-07-09: RPC (chain_id=177, ~2s blocks), explorer
# (https://explorer.hsk.xyz redirects to a real Blockscout instance at
# hsk.blockscout.com, confirmed via its own /api/v2/stats), and — critically
# — signal source: searched the last several thousand blocks for the
# canonical Uniswap V3, Uniswap V2, and Liquidity Book Swap topics and found
# zero events of any of them, on a chain otherwise doing ~45k tx/day. No DEX
# with meaningful volume exists here yet. Token transfer-flow monitoring
# (large ERC-20 movements) is the signal source instead — see
# Protocol.HASHKEY_FLOWS / EventType.TRANSFER in models/raw_event.py. This
# also fits the compliance/institutional-flow narrative better than DEX
# swap "alpha" for a compliance-first chain's judges/users.
#
# Token addresses below were read directly from Blockscout's token list
# (api/v2/tokens) and cross-checked for real recent Transfer activity —
# not from memory.

_HSK_USDT = TokenMeta("0xf1b50ed67a9e2cc94ad3c477779e2d4cbfff9029", "usdt", 6)
_HSK_WETH = TokenMeta("0xefd4bc9afd210517803f293ababd701caeecdfd0", "weth", 18)
_HSK_WHSK = TokenMeta("0xb210d2120d57b758ee163cffb43e73728c471cf1", "hsk", 18)

HASHKEY = ChainConfig(
    name="hashkey",
    chain_id=177,
    rpc_env="HASHKEY_RPC_URL",
    default_rpc="https://mainnet.hsk.xyz",
    explorer_base="https://hsk.blockscout.com",
    poa_middleware=False,   # OP-stack; confirmed empty extraData on live blocks
    poll_interval_s=10,     # ~2s blocks — poll more often than Mantle's 15s
    max_blocks_per_batch=200,
    native_token="hsk",
    pool_registry={
        # Not liquidity pools — each entry is a token contract monitored
        # for large Transfer events. token0 carries that token's own
        # decimals/price_key (there's no second leg for a transfer).
        "0xf1b50ed67a9e2cc94ad3c477779e2d4cbfff9029": PoolMeta("hashkey_flows", token0=_HSK_USDT),
        "0xefd4bc9afd210517803f293ababd701caeecdfd0": PoolMeta("hashkey_flows", token0=_HSK_WETH),
        "0xb210d2120d57b758ee163cffb43e73728c471cf1": PoolMeta("hashkey_flows", token0=_HSK_WHSK),
    },
    token_prices={
        # Static fallback — no verified Chainlink deployment found on
        # HashKey Chain yet, so unlike Arbitrum this has no price_feeds
        # overlay. HSK spot checked live via CoinGecko 2026-07-09 (~$0.081);
        # revisit before relying on this for real sizing decisions.
        "hsk":  0.081,
        "usdt": 1.0,
        "weth": 2400.0,
    },
)


# ── Ethereum ─────────────────────────────────────────────────────────────────
#
# Blue-chip Uniswap V3 pools only (2026-07-18), verified before adding —
# same discipline as the Mantle Agni-pool fix. Pool addresses came from the
# real Uniswap V3 Factory (0x1F98431c...) via getPool(), then confirmed live
# via eth_getLogs Swap-topic hits (WETH/USDC and WETH/USDT both swap nearly
# every block; WBTC/WETH had 8 real swaps in a 40-block/~8min window — all
# genuinely active, not guessed). Token0/token1 ordering read directly from
# each pool's token0()/token1(), not inferred from address comparison.
# Token addresses + decimals verified via symbol()/decimals(). Chainlink
# feed addresses verified via get_code() + description() before being added
# below — same rule as every other chain's price_feeds block.
#
# NOTE: free public Ethereum RPCs are far stricter on eth_getLogs than
# Mantle/Arbitrum's — during verification, ethereum.publicnode.com rejected
# any multi-block range on these pools as an "archive request," and 1rpc.io
# capped ranges at 10-50 blocks depending on the call. max_blocks_per_batch
# is set conservatively below; if ingestion sees rate-limit/archive errors
# in production, a paid RPC key will likely be needed sooner than it was
# for Arbitrum.
_ETH_WETH = TokenMeta("0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", "eth",  18)
_ETH_USDC = TokenMeta("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "usdc", 6)
_ETH_USDT = TokenMeta("0xdac17f958d2ee523a2206206994597c13d831ec7", "usdt", 6)
_ETH_WBTC = TokenMeta("0x2260fac5e5542a773aa44fbcfedf7c193bc2c599", "wbtc", 8)

ETHEREUM = ChainConfig(
    name="ethereum",
    chain_id=1,
    rpc_env="ETHEREUM_RPC_URL",
    default_rpc="https://ethereum.publicnode.com",
    explorer_base="https://etherscan.io",
    poa_middleware=False,
    poll_interval_s=15,
    max_blocks_per_batch=20,
    native_token="eth",
    pool_registry={
        # USDC (0xA0b8) < WETH (0xC02a) → token0=USDC, token1=WETH
        "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640": PoolMeta(
            "uniswap_v3", token0=_ETH_USDC, token1=_ETH_WETH,   # USDC/WETH 0.05%
        ),
        # WETH (0xC02a) < USDT (0xdAC1) → token0=WETH, token1=USDT
        "0x11b815efb8f581194ae79006d24e0d814b7697f6": PoolMeta(
            "uniswap_v3", token0=_ETH_WETH, token1=_ETH_USDT,   # WETH/USDT 0.05%
        ),
        # WBTC (0x2260) < WETH (0xC02a) → token0=WBTC, token1=WETH
        "0x4585fe77225b41b697c938b018e2ac67ac5a20c0": PoolMeta(
            "uniswap_v3", token0=_ETH_WBTC, token1=_ETH_WETH,   # WBTC/WETH 0.05%
        ),
    },
    token_prices={
        # Static fallback only — overridden at runtime by price_feeds below.
        # Spot-checked via CoinGecko 2026-07-18.
        "eth":  1841.0,
        "wbtc": 64082.0,
        "usdc": 1.0,
        "usdt": 1.0,
    },
    price_feeds={
        # Chainlink AggregatorV3 feeds on Ethereum mainnet — each verified
        # via get_code() + description() before being added here.
        "eth":  "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419",  # ETH / USD
        "wbtc": "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c",  # BTC / USD
        "usdc": "0x8fFfFfd4AfB6115b954Bd326cbe7B4BA576818f6",  # USDC / USD
        "usdt": "0x3E7d1eAB13ad0104d2750B8863b489D65364e32D",  # USDT / USD
    },
)


# ── Registry ──────────────────────────────────────────────────────────────────

ALL_CHAINS: dict[str, ChainConfig] = {
    "mantle":   MANTLE,
    "arbitrum": ARBITRUM,
    "hashkey":  HASHKEY,
    "ethereum": ETHEREUM,
}


def get_enabled_chains() -> list[ChainConfig]:
    """Return chains listed in CHAINS env var (default: mantle)."""
    names = [n.strip() for n in os.getenv("CHAINS", "mantle").split(",") if n.strip()]
    chains = []
    for name in names:
        if name in ALL_CHAINS:
            chains.append(ALL_CHAINS[name])
        else:
            log.warning("Unknown chain %r in CHAINS env var — skipping", name)
    return chains or [MANTLE]
