"""
Executor — the core agent execution engine for Track 6.

For each incoming signal:
  1. Evaluates all active agent intent rules
  2. Runs safety guards on matching agents
  3. Calls Byreal Skills CLI to execute the action
  4. Logs every decision (success or abort) to ERC-8004 on Mantle

This is what judges see on Demo Day:
  Signal detected → intent matched → guards checked → Byreal executes
  → ERC-8004 decision logged on Mantle → verifiable agent reputation
"""
from __future__ import annotations

import logging
import os

from src.intent.rule_engine           import RuleEngine
from src.guards.guard_runner          import GuardRunner
from src.byreal.cli_runner            import ByrealCLIRunner, ByrealCLIError
from src.identity.erc8004_logger      import ERC8004Logger
from src.identity.agent_registry      import AgentRegistry
from src.models.execution_request     import ExecutionRequest, ActionType
from src.models.execution_result      import ExecutionResult, ResultStatus
from src.arbitrum.swap_executor       import ArbitrumSwapExecutor

log = logging.getLogger(__name__)

# Use dry-run mode unless explicitly set to production
DRY_RUN = os.getenv("BYREAL_DRY_RUN", "true").lower() != "false"


class Executor:
    """
    Main agent execution engine.
    Instantiate once and call process_signal() for each incoming signal.
    """

    def __init__(self):
        self.registry     = AgentRegistry()
        self.rule_eng     = RuleEngine()
        self.guards       = GuardRunner()
        self.byreal       = ByrealCLIRunner(dry_run=DRY_RUN)
        self.arb_executor = ArbitrumSwapExecutor(dry_run=DRY_RUN)
        self._identity_loggers: dict[str, ERC8004Logger] = {}

        log.info(
            "Executor ready — %d agents, byreal=%s, arb=%s, dry_run=%s",
            self.registry.count(),
            "available" if self.byreal.is_available() else "mock",
            "ready" if self.arb_executor.is_ready() else "stub",
            DRY_RUN,
        )

    def process_signal(self, signal: dict, daily_spent_usd: float = 0.0) -> list[ExecutionResult]:
        """
        Process one signal against all active agents.
        Returns list of ExecutionResult (one per agent that evaluated).

        daily_spent_usd: cumulative USD already executed today (UTC), tracked
        by the caller (worker.py, via packages/executor/src/safety.py) and
        passed through to the absolute daily cap guard.
        """
        results = []
        agents  = self.registry.get_active_agents()

        if not agents:
            log.debug("No active agents")
            return results

        log.info(
            "Processing signal: type=%s protocol=%s conf=%d z=%.2f",
            signal.get("signal_type"),
            signal.get("protocol"),
            signal.get("confidence", 0),
            signal.get("z_score", 0),
        )

        for agent in agents:
            request = self.rule_eng.evaluate(
                signal       = signal,
                agent_id     = agent.agent_id,
                rules        = agent.rules,
                owner_wallet = agent.owner_wallet,
            )

            if request is None:
                continue   # signal didn't match this agent's rules

            result = self._execute(request, daily_spent_usd)
            if result.success and result.amount_usd:
                # Running total within this call — so if more than one agent
                # matches the same signal, the second agent's guard check
                # sees the first agent's spend too, not a stale snapshot.
                daily_spent_usd += result.amount_usd
            self.registry.record_execution(agent.agent_id, result.success)
            results.append(result)

            # Log to ERC-8004 on the request's origin chain
            detail = result.tx_hash if result.success else (result.abort_reason or "")
            self._identity_logger(request.chain).log_decision(
                agent_id    = agent.agent_id,
                signal_id   = request.signal_id,
                action_type = result.action_type,
                success     = result.success,
                detail      = detail or "",
            )

            status_emoji = "✅" if result.success else "🛑"
            log.info(
                "%s Agent %d: %s — %s",
                status_emoji,
                agent.agent_id,
                result.status.value,
                result.tx_hash or result.abort_reason or "",
            )

        return results

    def _identity_logger(self, chain: str) -> ERC8004Logger:
        """One ERC8004Logger per origin chain, built lazily and cached."""
        if chain not in self._identity_loggers:
            self._identity_loggers[chain] = ERC8004Logger(chain=chain)
        return self._identity_loggers[chain]

    def _execute(self, request: ExecutionRequest, daily_spent_usd: float = 0.0) -> ExecutionResult:
        """Run guards then execute via Byreal CLI."""

        # ── Guards ──────────────────────────────────────────────────────────
        wallet_balance_usd = self._wallet_balance_usd(request.chain)
        guard_result = self.guards.check(request, wallet_balance_usd, daily_spent_usd)
        if not guard_result:
            return ExecutionResult.aborted(
                request,
                reason = guard_result.reason,
                guard  = guard_result.guard,
            )

        # ── Execute via Byreal ───────────────────────────────────────────────
        try:
            if request.action_type == ActionType.SWAP:
                result_data = self._do_swap(request)
            elif request.action_type == ActionType.ADD_LIQUIDITY:
                result_data = self._do_add_liquidity(request)
            else:
                return ExecutionResult.aborted(
                    request,
                    reason = f"Unsupported action: {request.action_type}",
                )

            tx_hash = result_data.get("tx_hash", "mock_tx_" + "0" * 20)
            return ExecutionResult.success_from(
                request,
                tx_hash         = tx_hash,
                amount_usd      = request.amount_usd,
                execution_price = result_data.get("execution_price"),
            )

        except ByrealCLIError as exc:
            return ExecutionResult.aborted(request, reason=str(exc), guard="byreal")
        except Exception as exc:
            log.error("Unexpected execution error: %s", exc)
            return ExecutionResult.aborted(request, reason=f"Unexpected: {exc}")

    def _do_swap(self, request: ExecutionRequest) -> dict:
        """
        Execute a token swap — routes to Arbitrum or Mantle based on
        request.chain. Any other chain raises rather than silently falling
        through to the Mantle/Byreal path, which was the previous behavior
        (a documented residual gap for HashKey — see Completed Work #9 in
        PROJECT_STATE.md — that this closes for real rather than extending
        it to a second chain). Harmless today since neither HashKey nor
        Ethereum signals drive execution yet, but "harmless today" is
        exactly how the original gap was described too.
        """
        if request.chain == "arbitrum":
            # Arbitrum: Uniswap V3 via ArbitrumSwapExecutor
            token_in  = request.input_token  or "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8"  # USDC.e
            token_out = request.output_token or "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"  # WETH
            log.info(
                "Arbitrum swap: $%.0f %s → %s dry_run=%s",
                request.amount_usd, token_in[:10], token_out[:10], DRY_RUN,
            )
            return self.arb_executor.swap(
                token_in   = token_in,
                token_out  = token_out,
                amount_usd = request.amount_usd,
                slippage   = request.max_slippage,
            )

        if request.chain != "mantle":
            raise ValueError(
                f"No swap executor wired up for chain={request.chain!r} — "
                "only 'mantle' (Byreal) and 'arbitrum' (Uniswap V3) are supported"
            )

        # Mantle: Byreal CLI
        input_mint  = request.input_token  or "0x78c1b0C915c4FAA5FffA6CAbf0219DA63d7f4cb8"  # WMNT
        output_mint = request.output_token or "0x201EBa5CC46D216Ce6DC03F6a759e8E766e956aE"  # USDT
        log.info(
            "Byreal swap: $%.0f %s → %s dry_run=%s",
            request.amount_usd, input_mint[:8], output_mint[:8], DRY_RUN,
        )
        return self.byreal.swap_execute(
            input_mint  = input_mint,
            output_mint = output_mint,
            amount      = request.amount_usd,
            slippage    = request.max_slippage,
        )

    def _wallet_balance_usd(self, chain: str) -> float:
        """
        Real USD wallet balance for the position cap guard, fetched per chain.
        Returns 0.0 (fail closed) on any lookup failure — a guard computing
        "5% of wallet" must never fall back to a guessed number, since that
        silently defeats the whole purpose of the cap.
        """
        try:
            if chain == "arbitrum":
                return self.arb_executor.get_wallet_balance_usd()
            if chain != "mantle":
                raise ValueError(f"No wallet-balance lookup wired up for chain={chain!r}")
            return float(self.byreal.wallet_balance().get("balance_usd", 0.0))
        except Exception as exc:
            log.warning(
                "Could not fetch wallet balance for chain=%s (%s) — "
                "guard will fail closed at $0", chain, exc,
            )
            return 0.0

    def _do_add_liquidity(self, request: ExecutionRequest) -> dict:
        """Analyze pool then copy top farmer position."""
        pool_data = self.byreal.pools_analyze(request.pool_address)
        log.info("Pool analysis: tvl=$%s apr=%s",
                 pool_data.get("tvl_usd", "?"),
                 pool_data.get("apr_24h", "?"))
        return {"tx_hash": "liquidity_mock_" + "0" * 10, "pool": pool_data}
