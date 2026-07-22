#!/usr/bin/env python3
"""
Manual control for the Execute kill switch (see
packages/executor/src/safety.py) — halts the executor worker instantly,
with no redeploy, by setting a Redis key it checks every loop iteration.
Signals stay queued on mantis:signals:exec untouched while active; nothing
is dropped.

Usage:
  python scripts/execution_kill_switch.py on
  python scripts/execution_kill_switch.py off
  python scripts/execution_kill_switch.py status

Reads REDIS_URL the same way every worker does (env var, falls back to
localhost) — point it at the production Redis via the Railway public proxy
the same way scripts/gate_check.sh does, rather than baking a URL in here.
"""
from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "executor"))
from src.safety import KILL_SWITCH_KEY


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["on", "off", "status"])
    args = ap.parse_args()

    import redis
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    r = redis.from_url(redis_url, decode_responses=True)

    if args.action == "on":
        r.set(KILL_SWITCH_KEY, "1")
        print("🛑 Kill switch ON — executor will halt within one loop iteration (up to ~5s).")
        print("   Queued signals are NOT dropped; they'll process once you turn this off.")
    elif args.action == "off":
        r.delete(KILL_SWITCH_KEY)
        print("✅ Kill switch OFF — executor will resume processing.")
    else:
        active = bool(r.get(KILL_SWITCH_KEY))
        print(f"Kill switch is {'ACTIVE 🛑' if active else 'inactive ✅'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
