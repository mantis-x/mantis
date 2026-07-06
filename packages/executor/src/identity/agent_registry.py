"""
AgentRegistry — Postgres-backed registry of agents.

Each agent has:
  - An ERC-8004 on-chain identity (agent_id)
  - An owner wallet
  - Intent rules (what signals to act on)
  - Safety limits (max position, max slippage)

Previously in-memory: every Railway redeploy silently wiped every
registered agent and its execution stats back to just the seeded demo
agent. Now backed by the agents table via src/db/connection.py — a local
mirror of packages/shared/src/db, not a cross-package import (see
src/db/connection.py's docstring for why: every package's worker runs as
its own process with its own top-level `src` namespace, so two packages'
`src` trees can't both be imported as `src.*` in one process). The Alembic
migration that creates the agents table still lives in packages/shared.

Public method signatures are unchanged from the in-memory version so
executor.py/worker.py call sites didn't need to change, with one exception:
record_execution moved from the returned Agent object onto the registry
itself (registry.record_execution(agent_id, success)) — a plain dataclass
snapshot can't persist a mutation on its own, so the write has to go
through the registry, which owns the DB session.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

from src.db.connection import get_session
from src.db.models.agent import AgentRow


@dataclass
class Agent:
    """Detached snapshot of an agents-table row — safe to use after the session closes."""
    agent_id:     int
    owner_wallet: str
    name:         str
    rules:        dict
    active:       bool = True
    erc8004_token_id: Optional[int] = None
    total_decisions:  int = 0
    total_executions: int = 0
    total_aborts:     int = 0

    @property
    def success_rate(self) -> float:
        if self.total_decisions == 0:
            return 0.0
        return self.total_executions / self.total_decisions * 100

    @classmethod
    def _from_row(cls, row: AgentRow) -> "Agent":
        return cls(
            agent_id         = row.agent_id,
            owner_wallet     = row.owner_wallet,
            name             = row.name,
            rules            = row.rules,
            active           = row.active,
            erc8004_token_id = row.erc8004_token_id,
            total_decisions  = row.total_decisions,
            total_executions = row.total_executions,
            total_aborts     = row.total_aborts,
        )


class AgentRegistry:
    """Postgres-backed agent registry. Seeds the demo agent once, on first use."""

    def __init__(self):
        self._seed_demo_agent_if_empty()

    def _seed_demo_agent_if_empty(self) -> None:
        """
        Register the demo agent on first-ever startup only — unlike the old
        in-memory version, this must not reseed on every process restart,
        or every redeploy would create a duplicate demo agent row.
        """
        with get_session() as session:
            if session.query(AgentRow).count() > 0:
                return

            owner = os.getenv(
                "BYREAL_AGENT_WALLET", os.getenv("DEPLOYER_PRIVATE_KEY", "0x")[:4]
            )
            demo_rules = {
                "min_confidence": 75,
                "signal_types":   ["accumulation", "whale_entry"],
                "protocols":      ["agni_finance", "merchant_moe"],
                "min_z_score":    3.0,
                "action":         "swap",
                "amount_usd":     100.0,
                "max_slippage":   0.02,
                "max_position_pct": 5.0,
                "description": (
                    "Follow smart money into high-confidence accumulation "
                    "signals on Agni Finance and Merchant Moe."
                ),
            }
            row = AgentRow(
                owner_wallet=owner,
                name="Mantis Execute — Demo Agent",
                rules=demo_rules,
                active=True,
            )
            session.add(row)
            session.flush()
            log.info(
                "Demo agent registered: id=%d name='%s'", row.agent_id, row.name
            )

    def get_active_agents(self) -> list[Agent]:
        with get_session() as session:
            rows = session.query(AgentRow).filter(AgentRow.active.is_(True)).all()
            return [Agent._from_row(r) for r in rows]

    def get(self, agent_id: int) -> Optional[Agent]:
        with get_session() as session:
            row = session.query(AgentRow).filter(AgentRow.agent_id == agent_id).one_or_none()
            return Agent._from_row(row) if row else None

    def register(self, owner_wallet: str, name: str, rules: dict) -> Agent:
        with get_session() as session:
            row = AgentRow(owner_wallet=owner_wallet, name=name, rules=rules, active=True)
            session.add(row)
            session.flush()
            log.info("Agent registered: id=%d name='%s'", row.agent_id, name)
            return Agent._from_row(row)

    def record_execution(self, agent_id: int, success: bool) -> None:
        """
        Persist an execution outcome against an agent's stats. Replaces the
        old Agent.record_execution() instance method — a detached dataclass
        snapshot has nothing to commit through, so this must go through the
        registry (which owns the session) instead.
        """
        with get_session() as session:
            row = session.query(AgentRow).filter(AgentRow.agent_id == agent_id).one_or_none()
            if row is None:
                log.warning("record_execution: no agent with id=%d", agent_id)
                return
            row.total_decisions += 1
            if success:
                row.total_executions += 1
            else:
                row.total_aborts += 1

    def count(self) -> int:
        with get_session() as session:
            return session.query(AgentRow).count()
