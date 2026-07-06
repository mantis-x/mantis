"""
RuleEngine — evaluates whether an incoming signal matches
an agent's intent rules and produces an ExecutionRequest.

Intent rules are simple dicts stored per agent:
{
    "min_confidence":  75,
    "signal_types":    ["accumulation", "whale_entry"],
    "protocols":       ["agni_finance", "merchant_moe"],
    "min_z_score":     3.0,
    "action":          "swap",
    "amount_usd":      500.0,
    "description":     "Follow smart money into mETH pools"
}

All fields except min_confidence are optional.
"""
from __future__ import annotations

import logging
from typing import Optional

from src.models.execution_request import ExecutionRequest, ActionType

log = logging.getLogger(__name__)


class RuleEngine:
    """
    Evaluates signals against agent intent rules.
    Returns an ExecutionRequest if the signal matches, None otherwise.
    """

    def evaluate(
        self,
        signal:    dict,
        agent_id:  int,
        rules:     dict,
        owner_wallet: str,
    ) -> Optional[ExecutionRequest]:
        """
        Evaluate one signal against one agent's rules.
        Returns ExecutionRequest if match, None if no match.
        """
        # ── Filter checks ───────────────────────────────────────────────────

        # Minimum confidence
        min_conf = int(rules.get("min_confidence", 60))
        if signal.get("confidence", 0) < min_conf:
            log.debug(
                "Agent %d: confidence %d < %d — skip",
                agent_id, signal.get("confidence", 0), min_conf,
            )
            return None

        # Signal type filter
        allowed_types = rules.get("signal_types")
        if allowed_types and signal.get("signal_type") not in allowed_types:
            log.debug("Agent %d: signal_type %s not in %s — skip",
                      agent_id, signal.get("signal_type"), allowed_types)
            return None

        # Protocol filter
        allowed_protocols = rules.get("protocols")
        if allowed_protocols and signal.get("protocol") not in allowed_protocols:
            log.debug("Agent %d: protocol %s not in %s — skip",
                      agent_id, signal.get("protocol"), allowed_protocols)
            return None

        # Minimum z-score
        min_z = float(rules.get("min_z_score", 0.0))
        if float(signal.get("z_score", 0)) < min_z:
            log.debug("Agent %d: z_score %.2f < %.2f — skip",
                      agent_id, signal.get("z_score", 0), min_z)
            return None

        # ── Match — build ExecutionRequest ───────────────────────────────────
        action_str  = rules.get("action", "swap")
        try:
            action = ActionType(action_str)
        except ValueError:
            action = ActionType.SWAP

        amount_usd = float(rules.get("amount_usd", 100.0))

        log.info(
            "Agent %d matched signal: type=%s protocol=%s conf=%d → %s $%.0f",
            agent_id,
            signal.get("signal_type"),
            signal.get("protocol"),
            signal.get("confidence", 0),
            action.value,
            amount_usd,
        )

        return ExecutionRequest(
            agent_id     = agent_id,
            owner_wallet = owner_wallet,
            signal_id    = str(signal.get("id", "unknown")),
            signal_type  = signal.get("signal_type", ""),
            protocol     = signal.get("protocol", ""),
            pool_address = signal.get("pool_address", ""),
            confidence   = int(signal.get("confidence", 0)),
            z_score      = float(signal.get("z_score", 0)),
            action_type  = action,
            amount_usd   = amount_usd,
            max_slippage = float(rules.get("max_slippage", 0.02)),
            max_position = float(rules.get("max_position_pct", 5.0)),
            chain        = signal.get("chain", "mantle"),
        )
