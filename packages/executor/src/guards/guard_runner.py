"""
GuardRunner: runs all safety checks in sequence before any execution.
Returns (passed: bool, reason: str).

Checks run in order:
  1. position_cap  — would this breach max % of wallet balance?
  2. slippage      — is current quote within acceptable slippage?
  3. blacklist     — is the target contract flagged?

All three must pass. First failure short-circuits and returns the reason,
which is logged to the ERC-8004 identity record as an abort.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class GuardResult:
    passed: bool
    reason: str
    check_name: Optional[str] = None  # which guard failed, if any


class GuardRunner:
    def __init__(self, position_cap, slippage_check, blacklist):
        self._guards = [
            ("position_cap",  position_cap),
            ("slippage_check", slippage_check),
            ("blacklist",     blacklist),
        ]

    def run(self, execution_request) -> GuardResult:
        for name, guard in self._guards:
            passed, reason = guard.check(execution_request)
            if not passed:
                log.warning(
                    "Guard failed [%s]: agent=%s signal=%s reason=%s",
                    name,
                    execution_request.agent_id,
                    execution_request.signal_id,
                    reason,
                )
                return GuardResult(passed=False, reason=reason, check_name=name)

        return GuardResult(passed=True, reason="all guards passed")
