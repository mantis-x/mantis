#!/usr/bin/env python3
"""
Check RPC connectivity and block freshness for enabled chains.

Usage:
  python scripts/check_rpc_health.py                  # all CHAINS= chains
  python scripts/check_rpc_health.py --chain mantle
  python scripts/check_rpc_health.py --chain arbitrum
  python scripts/check_rpc_health.py --chain mantle,arbitrum
"""
from __future__ import annotations

import argparse
import os
import sys
import time

# Allow importing from ingestion package without installing it
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "ingestion", "src"))

from chains import ChainConfig, get_enabled_chains, MANTLE, ARBITRUM

ALL_CHAINS: dict[str, ChainConfig] = {
    "mantle":   MANTLE,
    "arbitrum": ARBITRUM,
}

# Block age threshold for a "stale" chain (seconds)
STALE_BLOCK_SECONDS = 120


def check_chain(config: ChainConfig) -> dict:
    """Return health dict for one chain."""
    from web3 import Web3
    try:
        from web3.middleware import ExtraDataToPOAMiddleware
    except ImportError:
        from web3.middleware import geth_poa_middleware as ExtraDataToPOAMiddleware

    result = {
        "chain":    config.name,
        "rpc_url":  config.rpc_url(),
        "ok":       False,
        "block":    None,
        "block_age_s": None,
        "latency_ms":  None,
        "error":    None,
    }

    try:
        w3 = Web3(Web3.HTTPProvider(result["rpc_url"], request_kwargs={"timeout": 10}))
        if config.poa_middleware:
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        t0 = time.time()
        connected = w3.is_connected()
        if not connected:
            result["error"] = "is_connected() returned False"
            return result

        block_num = w3.eth.block_number
        result["latency_ms"] = round((time.time() - t0) * 1000, 1)

        block = w3.eth.get_block(block_num)
        block_ts  = block["timestamp"]
        block_age = int(time.time()) - block_ts

        result["block"]      = block_num
        result["block_age_s"] = block_age
        result["ok"]         = block_age < STALE_BLOCK_SECONDS

        if not result["ok"]:
            result["error"] = f"block {block_num} is {block_age}s old (stale threshold {STALE_BLOCK_SECONDS}s)"

    except Exception as exc:
        result["error"] = str(exc)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Mantis RPC health")
    parser.add_argument(
        "--chain",
        default="",
        help="Comma-separated chain names to check (default: CHAINS env var or all)",
    )
    args = parser.parse_args()

    if args.chain:
        names = [c.strip() for c in args.chain.split(",") if c.strip()]
        configs = []
        for name in names:
            if name not in ALL_CHAINS:
                print(f"ERROR: unknown chain '{name}'. Known: {list(ALL_CHAINS)}")
                sys.exit(1)
            configs.append(ALL_CHAINS[name])
    else:
        configs = get_enabled_chains()

    if not configs:
        print("No chains configured. Set CHAINS=mantle or pass --chain mantle")
        sys.exit(1)

    all_ok = True
    for config in configs:
        r = check_chain(config)
        status = "OK" if r["ok"] else "FAIL"
        if r["ok"]:
            print(
                f"[{status}]  {r['chain']:10s}  block={r['block']}  "
                f"age={r['block_age_s']}s  latency={r['latency_ms']}ms  "
                f"rpc={r['rpc_url']}"
            )
        else:
            print(
                f"[{status}] {r['chain']:10s}  error={r['error']}  "
                f"rpc={r['rpc_url']}"
            )
            all_ok = False

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
