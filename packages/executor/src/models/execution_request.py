"""
ExecutionRequest — describes what the agent should do
when a signal matches an agent's intent rules.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional


class ActionType(str, Enum):
    SWAP            = "swap"
    ADD_LIQUIDITY   = "add_liquidity"
    REMOVE_LIQUIDITY= "remove_liquidity"
    ABORT           = "abort"


@dataclass
class ExecutionRequest:
    # Agent identity
    agent_id:       int
    owner_wallet:   str

    # Signal that triggered this request
    signal_id:      str
    signal_type:    str
    protocol:       str
    pool_address:   str
    confidence:     int
    z_score:        float

    # What to do
    action_type:    ActionType
    amount_usd:     float           # how much to deploy
    max_slippage:   float           # e.g. 0.02 = 2%
    max_position:   float           # max % of wallet balance

    # Byreal CLI params
    input_token:    Optional[str] = None
    output_token:   Optional[str] = None

    # Chain this execution runs on
    chain:          str = "mantle"

    # Untrusted client-provided World ID proof.  It is never accepted without
    # server-side verification by WorldIDVerifier.
    world_id_proof: Optional[Mapping[str, Any]] = None
    approval_expires_at: Optional[datetime] = None

    created_at: datetime = None

    def __post_init__(self):
        if self.created_at is None:
            from datetime import timezone
            self.created_at = datetime.now(tz=timezone.utc)
