#!/usr/bin/env python3
"""
Verify a candidate pool address before trusting it enough to add to
chains.py's pool_registry.

This project has been burned three times by skipping this: Agni's
pool_registry pointed at two token contracts (not pools) with zero real
Swap-topic hits ever (Completed Work #15); Merchant Moe/Fluxion had the
same problem — addresses with zero contract code deployed at all
(Completed Work #23). Every time, the fix was the same three manual RPC
checks, done by hand, one session at a time. This script makes those
checks reusable instead of relying on someone remembering to redo them.

Checks, in order (stops at the first failure):
  1. Real contract code is deployed at the address (eth_getCode != "0x")
  2. token0()/token1() (Uniswap V3 / Agni-style) or getTokenX()/getTokenY()
     (Merchant Moe / Trader Joe Liquidity Book) resolve to real addresses
  3. The pool actually emits its protocol's Swap topic within a recent
     block range (eth_getLogs) — deployed and has a matching function
     signature is not the same as genuinely active

Usage:
  python scripts/verify_pool.py --chain mantle --pool 0x1606c79b... --protocol merchant_moe
  python scripts/verify_pool.py --chain ethereum --pool 0x88e6... --protocol uniswap_v3 --blocks 5000

Exits nonzero if any check fails, so it can gate a pool_registry addition
(e.g. in a pre-commit check or just as a manual go/no-go) instead of
eyeballing eth_getLogs output by hand each time.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "ingestion"))

from src.chains import ALL_CHAINS
from src.decoders.event_normaliser import SWAP_TOPIC, UNIV3_SWAP_TOPIC, LB_SWAP_TOPIC

# Which Swap topic (and which token-getter functions) a protocol family uses.
# "uniswap_v3" covers every canonical V3 fork (Uniswap V3 itself, and any
# fork using the 5-field Swap signature); "agni_finance" is Mantle's
# 9-field variant; "merchant_moe" is Liquidity Book (Trader Joe fork).
PROTOCOL_TOPICS = {
    "uniswap_v3":   UNIV3_SWAP_TOPIC,
    "agni_finance": SWAP_TOPIC,
    "merchant_moe": LB_SWAP_TOPIC,
}

_V3_TOKEN_ABI = [
    {"inputs": [], "name": "token0", "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "token1", "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"},
]
_LB_TOKEN_ABI = [
    {"inputs": [], "name": "getTokenX", "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "getTokenY", "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"},
]

_ERC20_ABI = [
    {"inputs": [], "name": "symbol", "outputs": [{"type": "string"}], "stateMutability": "view", "type": "function"},
]


def _symbol(w3, address: str) -> str:
    try:
        return w3.eth.contract(address=address, abi=_ERC20_ABI).functions.symbol().call()
    except Exception:
        return "?"


def verify_pool(chain: str, pool: str, protocol: str, blocks: int) -> bool:
    from web3 import Web3
    try:
        from web3.middleware import ExtraDataToPOAMiddleware
    except ImportError:
        from web3.middleware import geth_poa_middleware as ExtraDataToPOAMiddleware

    if chain not in ALL_CHAINS:
        print(f"[FAIL] unknown chain '{chain}' — not in chains.py's ALL_CHAINS")
        return False
    if protocol not in PROTOCOL_TOPICS:
        print(f"[FAIL] unknown protocol '{protocol}' — supported: {sorted(PROTOCOL_TOPICS)}")
        return False

    config = ALL_CHAINS[chain]
    pool = Web3.to_checksum_address(pool)
    w3 = Web3(Web3.HTTPProvider(config.rpc_url(), request_kwargs={"timeout": 15}))
    if config.poa_middleware:
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    print(f"Verifying {pool} on {chain} as {protocol}...\n")

    # 1. Real contract code deployed
    code = w3.eth.get_code(pool)
    if not code or code == b"" or code.hex() in ("", "0x"):
        print(f"[FAIL] no contract code at {pool} — this is not a deployed contract at all")
        return False
    print(f"[ok] contract code present ({len(code)} bytes)")

    # 2. Token getters resolve to real addresses
    is_lb = protocol == "merchant_moe"
    abi = _LB_TOKEN_ABI if is_lb else _V3_TOKEN_ABI
    fn_x, fn_y = ("getTokenX", "getTokenY") if is_lb else ("token0", "token1")
    try:
        contract = w3.eth.contract(address=pool, abi=abi)
        token_x = getattr(contract.functions, fn_x)().call()
        token_y = getattr(contract.functions, fn_y)().call()
    except Exception as exc:
        print(f"[FAIL] {fn_x}()/{fn_y}() call failed — likely not a {protocol} pool contract: {exc}")
        return False
    sym_x, sym_y = _symbol(w3, token_x), _symbol(w3, token_y)
    print(f"[ok] {fn_x}()={token_x} ({sym_x})  {fn_y}()={token_y} ({sym_y})")

    # 3. Genuinely active — real Swap-topic hits in a recent block sample
    topic = PROTOCOL_TOPICS[protocol]
    latest = w3.eth.block_number
    from_block = max(0, latest - blocks)
    try:
        logs = w3.eth.get_logs({
            "address": pool, "fromBlock": from_block, "toBlock": latest, "topics": [topic],
        })
    except Exception as exc:
        print(
            f"[FAIL] eth_getLogs failed over {blocks} blocks ({exc}) — "
            f"this RPC may cap the range; retry with a smaller --blocks"
        )
        return False

    if not logs:
        print(f"[FAIL] zero Swap-topic hits in the last {blocks} blocks — deployed but not genuinely active")
        return False
    print(f"[ok] {len(logs)} real Swap-topic hit(s) in the last {blocks} blocks")

    print(f"\n[PASS] {pool} looks like a real, active {protocol} pool on {chain}.")
    return True


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chain", required=True, help="Chain slug from chains.py's ALL_CHAINS (e.g. mantle, arbitrum, ethereum)")
    ap.add_argument("--pool", required=True, help="Candidate pool contract address")
    ap.add_argument("--protocol", required=True, choices=sorted(PROTOCOL_TOPICS), help="Pool protocol family")
    ap.add_argument("--blocks", type=int, default=2000, help="Block sample size for the Swap-topic activity check (default 2000)")
    args = ap.parse_args()

    ok = verify_pool(args.chain, args.pool, args.protocol, args.blocks)
    sys.exit(0 if ok else 1)
