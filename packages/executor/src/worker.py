"""
Executor Worker — entry point for Mantis Execute (Track 6).
Run: python -m src.worker (from packages/executor/)

Listens on Redis mantis:signals:exec — a dedicated FIFO queue populated
by the enrichment worker (see packages/enrichment/src/worker.py), separate
from mantis:signals which the delivery worker drains independently. Each
worker gets its own exclusive copy of every signal, so slow/down execution
can never cause delivery to miss a signal, and vice versa.

Evaluates each signal against registered agent intent rules,
and executes via Byreal Skills CLI when rules match.

Every decision is logged to AgentIdentity.sol on the signal's origin chain.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".env"
))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mantis.executor")

from src.executor import Executor
from src.safety import (
    is_kill_switch_active, is_duplicate_signal,
    get_daily_spent_usd, record_execution_spend,
)


async def main() -> None:
    log.info("=" * 55)
    log.info("  Mantis Execute — Agentic Wallet (Track 6)")
    log.info("  Byreal Skills CLI + ERC-8004 identity")
    log.info("=" * 55)

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    dry_run   = os.getenv("BYREAL_DRY_RUN", "true").lower() != "false"

    log.info("Mode: %s", "DRY RUN (simulation)" if dry_run else "LIVE EXECUTION")

    executor = Executor()

    log.info(
        "Identity contract: %s (routed per origin chain — mantle/arbitrum/hashkey)",
        os.getenv("AGENT_IDENTITY_CONTRACT_ADDRESS", "not set")[:20],
    )

    import redis.asyncio as aioredis
    r = aioredis.from_url(redis_url, decode_responses=True)

    log.info("Listening on Redis mantis:signals:exec ...")
    log.info("Kill switch: SET mantis:execution:kill_switch to halt instantly (no redeploy)")

    processed = 0
    executed  = 0
    killed_logged = False

    while True:
        try:
            # Kill switch is checked BEFORE popping anything off the queue,
            # so an active kill switch leaves signals queued untouched
            # rather than dropping them — "halt", not "discard".
            if await is_kill_switch_active(r):
                if not killed_logged:
                    log.warning(
                        "🛑 Execution kill switch ACTIVE — halting, signals "
                        "will queue untouched until mantis:execution:kill_switch is cleared"
                    )
                    killed_logged = True
                await asyncio.sleep(5)
                continue
            killed_logged = False

            # Atomic blocking pop — the returned item is guaranteed to be
            # removed by this call, so there's no separate peek-then-delete
            # step that could race with a concurrent push.
            item = await r.blpop("mantis:signals:exec", timeout=5)
            if item is None:
                continue

            _, raw = item
            signal = json.loads(raw)

            # Idempotency guard: skip a signal that's already been claimed
            # for execution (a replay, a manual Redis restore, a duplicate
            # push) rather than risk double-submitting a trade.
            if await is_duplicate_signal(r, signal):
                log.warning(
                    "Duplicate signal on exec queue, skipping — chain=%s protocol=%s",
                    signal.get("chain"), signal.get("protocol"),
                )
                continue

            processed += 1

            daily_spent_usd = await get_daily_spent_usd(r)
            results = executor.process_signal(signal, daily_spent_usd=daily_spent_usd)

            for result in results:
                if result.success:
                    executed += 1
                    if result.amount_usd:
                        await record_execution_spend(r, result.amount_usd)
                    log.info(
                        "🚀 Executed: agent=%d tx=%s amount=$%.0f",
                        result.agent_id,
                        result.tx_hash or "?",
                        result.amount_usd or 0,
                    )
                else:
                    log.info(
                        "🛑 Aborted: agent=%d guard=%s reason=%s",
                        result.agent_id,
                        result.guard_failed or "none",
                        result.abort_reason or "",
                    )

            # Store execution results for audit
            for result in results:
                await r.lpush(
                    "mantis:executions",
                    json.dumps(result.to_dict())
                )
            await r.ltrim("mantis:executions", 0, 999)

            if processed % 5 == 0:
                log.info(
                    "Stats: processed=%d executed=%d agents=%d",
                    processed, executed, executor.registry.count(),
                )

            await asyncio.sleep(0.1)

        except KeyboardInterrupt:
            break
        except Exception as exc:
            log.warning("Executor error: %s", exc)
            await asyncio.sleep(2)

    log.info("Executor stopped — processed=%d executed=%d", processed, executed)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Mantis Execute stopped")
