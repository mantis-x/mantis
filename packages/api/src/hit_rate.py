"""Hit-rate definition — mirror of packages/shared/src/tracking/
signal_outcome_tracker.py's DIRECTIONAL_SIGNAL_TYPES / is_hit(), so /v1/stats
reports the same notion of a "hit" the rest of the system uses. Keep in sync
if the source-of-truth changes.
"""
from __future__ import annotations

DIRECTIONAL_SIGNAL_TYPES = {
    "accumulation": "up",
    "whale_entry":  "up",
    "distribution": "down",
    "whale_exit":   "down",
}

HIT_RATE_HORIZON = "24h"


def is_hit(signal_type: str, pct_change: float | None) -> bool | None:
    """None if signal_type has no directional claim (e.g. unusual_volume/liquidity_added)."""
    direction = DIRECTIONAL_SIGNAL_TYPES.get(signal_type)
    if direction is None or pct_change is None:
        return None
    return pct_change > 0 if direction == "up" else pct_change < 0
