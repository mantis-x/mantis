#!/usr/bin/env python3
"""
backtest_signals.py — Mantis's track record report.

Reads the durable signals/signal_outcomes tables (populated by the tracking
worker, packages/shared/src/tracking/worker.py) and reports, per horizon:
  - how many signals have a completed outcome
  - hit rate: for directional signal types (accumulation/whale_entry expect
    the asset to rise; distribution/whale_exit expect it to fall), the % of
    completed outcomes where the price actually moved that way
  - average % price move
  - breakdown by chain, protocol, and signal_type

Usage:
    python scripts/backtest_signals.py [--horizon 24h] [--output report.json]

Requires DATABASE_URL (or the default local Postgres) with the tracking
worker having run for at least one horizon's worth of time — a signal only
contributes to a horizon's stats once its outcome status is "completed".
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "shared"))

from src.db.connection import get_session
from src.db.models.signal import SignalRow
from src.db.models.signal_outcome import SignalOutcomeRow, OutcomeStatus, HORIZONS_HOURS
from src.tracking.signal_outcome_tracker import DIRECTIONAL_SIGNAL_TYPES


def _is_hit(signal_type: str, pct_change: float) -> bool | None:
    """None if signal_type has no directional claim (e.g. unusual_volume)."""
    direction = DIRECTIONAL_SIGNAL_TYPES.get(signal_type)
    if direction is None or pct_change is None:
        return None
    return pct_change > 0 if direction == "up" else pct_change < 0


def build_report(horizon_filter: str | None = None) -> dict:
    with get_session() as session:
        rows = (
            session.query(SignalOutcomeRow, SignalRow)
            .join(SignalRow, SignalOutcomeRow.signal_id == SignalRow.id)
            .filter(SignalOutcomeRow.status == OutcomeStatus.COMPLETED)
            .all()
        )

    by_horizon: dict = defaultdict(lambda: {"n": 0, "hits": 0, "scored": 0, "pct_sum": 0.0})
    by_chain:   dict = defaultdict(lambda: {"n": 0, "pct_sum": 0.0})
    by_protocol: dict = defaultdict(lambda: {"n": 0, "pct_sum": 0.0})
    by_signal_type: dict = defaultdict(lambda: {"n": 0, "hits": 0, "scored": 0})

    for outcome, signal in rows:
        if horizon_filter and outcome.horizon_label != horizon_filter:
            continue

        h = by_horizon[outcome.horizon_label]
        h["n"] += 1
        h["pct_sum"] += outcome.pct_change or 0.0
        hit = _is_hit(signal.signal_type, outcome.pct_change)
        if hit is not None:
            h["scored"] += 1
            h["hits"] += int(hit)

        c = by_chain[signal.chain]
        c["n"] += 1
        c["pct_sum"] += outcome.pct_change or 0.0

        p = by_protocol[signal.protocol]
        p["n"] += 1
        p["pct_sum"] += outcome.pct_change or 0.0

        st = by_signal_type[signal.signal_type]
        st["n"] += 1
        if hit is not None:
            st["scored"] += 1
            st["hits"] += int(hit)

    def _finish_horizon(d):
        return {
            label: {
                "signals":      v["n"],
                "avg_pct_move": round(v["pct_sum"] / v["n"], 3) if v["n"] else None,
                "hit_rate_pct": round(v["hits"] / v["scored"] * 100, 1) if v["scored"] else None,
                "scored":       v["scored"],
            }
            for label, v in d.items()
        }

    def _finish_simple(d):
        return {
            k: {"signals": v["n"], "avg_pct_move": round(v["pct_sum"] / v["n"], 3) if v["n"] else None}
            for k, v in d.items()
        }

    def _finish_signal_type(d):
        return {
            k: {
                "signals":      v["n"],
                "hit_rate_pct": round(v["hits"] / v["scored"] * 100, 1) if v["scored"] else None,
                "scored":       v["scored"],
            }
            for k, v in d.items()
        }

    return {
        "total_completed_outcomes": len(rows),
        "by_horizon":     _finish_horizon(by_horizon),
        "by_chain":       _finish_simple(by_chain),
        "by_protocol":    _finish_simple(by_protocol),
        "by_signal_type": _finish_signal_type(by_signal_type),
    }


def print_report(report: dict) -> None:
    print(f"\nMantis track record — {report['total_completed_outcomes']} completed outcome(s)\n")

    print("By horizon:")
    for label in HORIZONS_HOURS:
        stats = report["by_horizon"].get(label)
        if not stats:
            print(f"  {label:>4}  (no completed outcomes yet)")
            continue
        hit = f"{stats['hit_rate_pct']}%" if stats["hit_rate_pct"] is not None else "n/a"
        print(
            f"  {label:>4}  n={stats['signals']:<5} "
            f"avg_move={stats['avg_pct_move']:+.2f}%  hit_rate={hit} (scored={stats['scored']})"
        )

    print("\nBy chain:")
    for chain, stats in sorted(report["by_chain"].items()):
        print(f"  {chain:<10} n={stats['signals']:<5} avg_move={stats['avg_pct_move']:+.2f}%")

    print("\nBy protocol:")
    for protocol, stats in sorted(report["by_protocol"].items()):
        print(f"  {protocol:<16} n={stats['signals']:<5} avg_move={stats['avg_pct_move']:+.2f}%")

    print("\nBy signal type:")
    for st, stats in sorted(report["by_signal_type"].items()):
        hit = f"{stats['hit_rate_pct']}%" if stats["hit_rate_pct"] is not None else "n/a (no directional claim)"
        print(f"  {st:<14} n={stats['signals']:<5} hit_rate={hit}")
    print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Mantis signal track record / backtest report")
    ap.add_argument("--horizon", choices=list(HORIZONS_HOURS.keys()), default=None,
                    help="Restrict the by-chain/protocol/signal_type breakdown to one horizon")
    ap.add_argument("--output", default=None, help="Write the report as JSON to this path")
    args = ap.parse_args()

    report = build_report(horizon_filter=args.horizon)
    print_report(report)

    if args.output:
        Path(args.output).write_text(json.dumps(report, indent=2))
        print(f"[ok] wrote {args.output}")
