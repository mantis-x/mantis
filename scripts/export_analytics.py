#!/usr/bin/env python3
"""
export_analytics.py — pull live Mantis metrics from Redis and write analytics.json.

Usage:
    python scripts/export_analytics.py [--output docs/analytics.json]

The JSON is read by docs/analytics.html to power the live dashboard.
If Redis is unreachable the script falls back to the last cached file.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

REDIS_URL  = os.getenv("REDIS_URL", "redis://localhost:6379/0")
SIGNAL_KEY = "mantis:signals"
EXEC_KEY   = "mantis:executions"
MAX_ITEMS  = 1000   # read at most this many items from each list


def main(output: str) -> None:
    try:
        import redis
        r = redis.from_url(REDIS_URL, decode_responses=True)
        r.ping()
        connected = True
    except Exception as exc:
        print(f"[warn] Redis unreachable ({exc}); using last cached file", file=sys.stderr)
        connected = False

    signals    = _load_list(r, SIGNAL_KEY, MAX_ITEMS) if connected else []
    executions = _load_list(r, EXEC_KEY,  MAX_ITEMS) if connected else []

    # ── Aggregate ─────────────────────────────────────────────────────────────

    chain_counts:    dict[str, int] = defaultdict(int)
    protocol_counts: dict[str, int] = defaultdict(int)
    day_counts:      dict[str, int] = defaultdict(int)

    for raw in signals:
        try:
            s = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            continue
        chain_counts[s.get("chain", "unknown")]    += 1
        protocol_counts[s.get("protocol", "unknown")] += 1
        ts = s.get("timestamp")
        if ts:
            try:
                day = datetime.fromisoformat(ts).strftime("%Y-%m-%d")
                day_counts[day] += 1
            except Exception:
                pass

    total_exec     = len(executions)
    success_exec   = sum(
        1 for raw in executions
        if _parse_bool(json.loads(raw).get("success") if isinstance(raw, str) else raw.get("success"))
    )

    # Signals over last 7 days (fill zeros for missing days)
    today = datetime.now(tz=timezone.utc).date()
    signals_7d = []
    for i in range(6, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        signals_7d.append({"day": day, "count": day_counts.get(day, 0)})

    # Recent executions for the log table
    recent_execs = []
    for raw in executions[:20]:
        try:
            e = json.loads(raw) if isinstance(raw, str) else raw
            recent_execs.append({
                "agent_id":    e.get("agent_id"),
                "chain":       e.get("chain", "mantle"),
                "action":      e.get("action_type", "swap"),
                "success":     _parse_bool(e.get("success")),
                "tx_hash":     e.get("tx_hash"),
                "amount_usd":  e.get("amount_usd"),
                "abort_reason":e.get("abort_reason"),
                "timestamp":   e.get("created_at"),
            })
        except Exception:
            continue

    payload = {
        "generated_at":    datetime.now(tz=timezone.utc).isoformat(),
        "redis_connected": connected,
        "totals": {
            "signals":      sum(chain_counts.values()),
            "executions":   total_exec,
            "success":      success_exec,
            "success_rate": round(success_exec / total_exec * 100, 1) if total_exec else 0,
        },
        "chains":          dict(chain_counts),
        "protocols":       dict(protocol_counts),
        "signals_7d":      signals_7d,
        "recent_executions": recent_execs,
    }

    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    print(f"[ok] wrote {out}  ({sum(chain_counts.values())} signals, "
          f"{total_exec} execs, redis={'yes' if connected else 'no'})")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_list(r, key: str, n: int) -> list:
    try:
        return r.lrange(key, 0, n - 1)
    except Exception as exc:
        print(f"[warn] could not read {key}: {exc}", file=sys.stderr)
        return []


def _parse_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes")
    return bool(v)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Export Mantis analytics to JSON")
    ap.add_argument("--output", default="docs/analytics.json",
                    help="Output file path (default: docs/analytics.json)")
    args = ap.parse_args()
    main(args.output)
