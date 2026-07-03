#!/usr/bin/env python3
"""
Replay a historical block range for a given chain and print decoded events.

Useful for:
  - Verifying USD estimation is sane against known whale blocks
  - Debugging decoder issues without waiting for live events
  - Checking pool registry coverage

Usage:
  python scripts/replay_block_range.py --chain arbitrum --from 300000000 --to 300000500
  python scripts/replay_block_range.py --chain mantle   --from 75000000  --to 75000050
  python scripts/replay_block_range.py --chain arbitrum --from 300000000 --to 300000500 --min-usd 100000
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "ingestion", "src"))

from chains import ChainConfig, MANTLE, ARBITRUM

ALL_CHAINS: dict[str, ChainConfig] = {
    "mantle":   MANTLE,
    "arbitrum": ARBITRUM,
}


def replay(config: ChainConfig, from_block: int, to_block: int, min_usd: float) -> None:
    from web3 import Web3
    from decoders.event_normaliser import (
        EventNormaliser,
        SWAP_TOPIC, UNIV3_SWAP_TOPIC, MINT_TOPIC, BURN_TOPIC, LB_SWAP_TOPIC,
    )
    try:
        from web3.middleware import ExtraDataToPOAMiddleware
    except ImportError:
        from web3.middleware import geth_poa_middleware as ExtraDataToPOAMiddleware

    rpc_url = config.rpc_url()
    print(f"Connecting to {config.name} via {rpc_url}")

    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
    if config.poa_middleware:
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    if not w3.is_connected():
        print(f"ERROR: cannot connect to {rpc_url}")
        sys.exit(1)

    normaliser = EventNormaliser(config.pool_registry)
    all_topics  = [SWAP_TOPIC, UNIV3_SWAP_TOPIC, MINT_TOPIC, BURN_TOPIC, LB_SWAP_TOPIC]
    addresses   = [Web3.to_checksum_address(a) for a in config.pool_registry]

    if not addresses:
        print(f"WARNING: no pools registered for {config.name}")
        return

    batch_size = config.max_blocks_per_batch
    total_events = 0
    total_blocks = 0

    print(
        f"Replaying {config.name} blocks {from_block}–{to_block} "
        f"({to_block - from_block + 1} blocks, batch={batch_size})"
    )
    print(f"Tracking {len(addresses)} pools  |  min_usd filter: ${min_usd:,.0f}")
    print("-" * 72)

    cursor = from_block
    while cursor <= to_block:
        end = min(cursor + batch_size - 1, to_block)

        try:
            raw_logs = w3.eth.get_logs({
                "fromBlock": cursor,
                "toBlock":   end,
                "address":   addresses,
                "topics":    [all_topics],
            })
        except Exception as exc:
            print(f"  [blocks {cursor}–{end}] get_logs error: {exc}")
            cursor = end + 1
            continue

        for raw_log in raw_logs:
            block_num = (
                int(raw_log["blockNumber"], 16)
                if isinstance(raw_log["blockNumber"], str)
                else raw_log["blockNumber"]
            )
            try:
                block_ts = w3.eth.get_block(block_num)["timestamp"]
            except Exception:
                block_ts = int(time.time())

            event = normaliser.normalise(
                raw_log, block_ts, config.token_prices, chain=config.name
            )
            if event is None:
                continue

            if event.amount_usd < min_usd:
                continue

            total_events += 1
            print(
                f"  block={block_num:>12d}  {event.event_type.value:5s}  "
                f"{event.protocol.value:15s}  usd={event.amount_usd:>12,.0f}  "
                f"pool={event.pool_address[:10]}  wallet={event.wallet_address[:10]}"
            )

        total_blocks += (end - cursor + 1)
        cursor = end + 1

    print("-" * 72)
    print(f"Done. {total_blocks} blocks scanned, {total_events} events matched (min_usd=${min_usd:,.0f})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay Mantis block range")
    parser.add_argument("--chain",   required=True, help="Chain name: mantle | arbitrum")
    parser.add_argument("--from",    dest="from_block", type=int, required=True, help="Start block number")
    parser.add_argument("--to",      dest="to_block",   type=int, required=True, help="End block number (inclusive)")
    parser.add_argument("--min-usd", type=float, default=0.0, help="Minimum USD value to print (default: 0)")
    args = parser.parse_args()

    chain = args.chain.lower()
    if chain not in ALL_CHAINS:
        print(f"ERROR: unknown chain '{chain}'. Known: {list(ALL_CHAINS)}")
        sys.exit(1)

    if args.from_block > args.to_block:
        print("ERROR: --from must be <= --to")
        sys.exit(1)

    replay(ALL_CHAINS[chain], args.from_block, args.to_block, args.min_usd)


if __name__ == "__main__":
    main()
