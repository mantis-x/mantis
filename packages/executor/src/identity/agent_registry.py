"""
AgentRegistry — manages the list of registered agents in memory.

Each agent has:
  - An ERC-8004 on-chain identity (agent_id)
  - An owner wallet
  - Intent rules (what signals to act on)
  - Safety limits (max position, max slippage)

Production: replace with Postgres agents table.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class Agent:
    agent_id:     int
    owner_wallet: str
    name:         str
    rules:        dict        # intent matching rules
    active:       bool = True

    # On-chain identity (set after mintAgent)
    erc8004_token_id: Optional[int] = None

    # Stats
    total_decisions:  int = 0
    total_executions: int = 0
    total_aborts:     int = 0

    def record_execution(self, success: bool) -> None:
        self.total_decisions += 1
        if success:
            self.total_executions += 1
        else:
            self.total_aborts += 1

    @property
    def success_rate(self) -> float:
        if self.total_decisions == 0:
            return 0.0
        return self.total_executions / self.total_decisions * 100


class AgentRegistry:
    """In-memory agent registry. Pre-populated with one demo agent."""

    def __init__(self):
        self._agents: dict[int, Agent] = {}
        self._next_id = 1
        self._seed_demo_agent()

    def _seed_demo_agent(self) -> None:
        """
        Register the demo agent for Demo Day.
        Intent: follow smart money accumulation on Agni Finance + Merchant Moe
        with confidence >= 75 and z-score >= 3.0.
        """
        owner = os.getenv("BYREAL_AGENT_WALLET",
                os.getenv("DEPLOYER_PRIVATE_KEY", "0x")[:4])

        demo_rules = {
            "min_confidence": 75,
            "signal_types":   ["accumulation", "whale_entry"],
            "protocols":      ["agni_finance", "merchant_moe"],
            "min_z_score":    3.0,
            "action":         "swap",
            "amount_usd":     100.0,      # conservative for hackathon
            "max_slippage":   0.02,
            "max_position_pct": 5.0,
            "description": (
                "Follow smart money into high-confidence accumulation "
                "signals on Agni Finance and Merchant Moe."
            ),
        }

        agent = Agent(
            agent_id     = 0,
            owner_wallet = owner,
            name         = "Mantis Execute — Demo Agent",
            rules        = demo_rules,
            active       = True,
        )
        self._agents[0] = agent
        log.info(
            "Demo agent registered: id=0 name='%s' rules=%s",
            agent.name, demo_rules.get("description", "")
        )

    def get_active_agents(self) -> list[Agent]:
        return [a for a in self._agents.values() if a.active]

    def get(self, agent_id: int) -> Optional[Agent]:
        return self._agents.get(agent_id)

    def register(
        self,
        owner_wallet: str,
        name:         str,
        rules:        dict,
    ) -> Agent:
        agent_id = self._next_id
        self._next_id += 1
        agent = Agent(
            agent_id     = agent_id,
            owner_wallet = owner_wallet,
            name         = name,
            rules        = rules,
        )
        self._agents[agent_id] = agent
        log.info("Agent registered: id=%d name='%s'", agent_id, name)
        return agent

    def count(self) -> int:
        return len(self._agents)
