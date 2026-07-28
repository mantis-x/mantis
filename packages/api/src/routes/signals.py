"""The read feed — /v1/signals, /v1/signals/{id}, /v1/stats.

Reads the durable Postgres `signals` table (+ its `signal_outcomes`), never
the capped Redis lists. All routes require a valid API key (see auth.py);
when API_TIER_ENABLED is false they 404 via that dependency.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from src.auth import ApiKeyContext, require_api_key
from src.config import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from src.db.connection import get_session
from src.db.models.signal import SignalRow
from src.db.models.signal_outcome import SignalOutcomeRow
from src.hit_rate import HIT_RATE_HORIZON, is_hit
from src.schemas import (
    ChainStatOut,
    OutcomeOut,
    SignalListOut,
    SignalOut,
    StatsOut,
)

router = APIRouter()


def _to_signal_out(row: SignalRow) -> SignalOut:
    return SignalOut(
        id=row.id,
        chain=row.chain,
        protocol=row.protocol,
        pool_address=row.pool_address,
        wallets=list(row.wallets or []),
        signal_type=row.signal_type,
        confidence=row.confidence,
        summary=row.summary,
        key_factors=list(row.key_factors or []),
        detected_at=row.detected_at.isoformat(),
        z_score=row.z_score,
        total_volume_usd=row.total_volume_usd,
        event_type=row.event_type,
        audit_tx_hash=row.audit_tx_hash,
        outcomes=[
            OutcomeOut(
                horizon=o.horizon_label,
                status=o.status,
                pct_change=o.pct_change,
                entry_price_usd=o.entry_price_usd,
                price_usd=o.price_usd,
            )
            for o in sorted(row.outcomes, key=lambda o: o.due_at)
        ],
    )


@router.get("/v1/signals", response_model=SignalListOut, tags=["signals"])
def list_signals(
    _ctx: ApiKeyContext = Depends(require_api_key),
    chain: str | None = Query(None),
    signal_type: str | None = Query(None),
    min_confidence: int | None = Query(None, ge=0, le=100),
    since: datetime | None = Query(None, description="ISO 8601; only signals detected at/after this"),
    cursor: int | None = Query(None, description="id of the last row from the previous page"),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1),
) -> SignalListOut:
    limit = min(limit, MAX_PAGE_LIMIT)

    with get_session() as session:
        q = session.query(SignalRow).options(selectinload(SignalRow.outcomes))
        if chain is not None:
            q = q.filter(SignalRow.chain == chain)
        if signal_type is not None:
            q = q.filter(SignalRow.signal_type == signal_type)
        if min_confidence is not None:
            q = q.filter(SignalRow.confidence >= min_confidence)
        if since is not None:
            q = q.filter(SignalRow.detected_at >= since)
        if cursor is not None:
            q = q.filter(SignalRow.id < cursor)   # keyset pagination, id desc

        rows = q.order_by(SignalRow.id.desc()).limit(limit + 1).all()

        next_cursor = None
        if len(rows) > limit:
            rows = rows[:limit]
            next_cursor = str(rows[-1].id)

        data = [_to_signal_out(r) for r in rows]

    return SignalListOut(data=data, next_cursor=next_cursor)


@router.get("/v1/signals/{signal_id}", response_model=SignalOut, tags=["signals"])
def get_signal(
    signal_id: int,
    _ctx: ApiKeyContext = Depends(require_api_key),
) -> SignalOut:
    with get_session() as session:
        row = (
            session.query(SignalRow)
            .options(selectinload(SignalRow.outcomes))
            .filter(SignalRow.id == signal_id)
            .first()
        )
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Signal not found")
        return _to_signal_out(row)


@router.get("/v1/stats", response_model=StatsOut, tags=["signals"])
def stats(_ctx: ApiKeyContext = Depends(require_api_key)) -> StatsOut:
    """Aggregate track record — signal counts per chain plus a 24h directional
    hit-rate over resolved outcomes (the real institutional value-add)."""
    with get_session() as session:
        total = session.query(func.count(SignalRow.id)).scalar() or 0

        counts = dict(
            session.query(SignalRow.chain, func.count(SignalRow.id))
            .group_by(SignalRow.chain)
            .all()
        )

        # (chain, signal_type, pct_change) for resolved 24h outcomes only.
        resolved_rows = (
            session.query(SignalRow.chain, SignalRow.signal_type, SignalOutcomeRow.pct_change)
            .join(SignalOutcomeRow, SignalOutcomeRow.signal_id == SignalRow.id)
            .filter(SignalOutcomeRow.horizon_label == HIT_RATE_HORIZON)
            .filter(SignalOutcomeRow.pct_change.isnot(None))
            .all()
        )

    resolved_by_chain: dict[str, int] = {}
    hits_by_chain: dict[str, int] = {}
    for chain, signal_type, pct_change in resolved_rows:
        hit = is_hit(signal_type, pct_change)
        if hit is None:
            continue   # non-directional — excluded from hit-rate
        resolved_by_chain[chain] = resolved_by_chain.get(chain, 0) + 1
        if hit:
            hits_by_chain[chain] = hits_by_chain.get(chain, 0) + 1

    by_chain = []
    for chain in sorted(counts):
        resolved = resolved_by_chain.get(chain, 0)
        hits = hits_by_chain.get(chain, 0)
        by_chain.append(
            ChainStatOut(
                chain=chain,
                signals=counts[chain],
                resolved=resolved,
                hit_rate=(round(hits / resolved, 4) if resolved > 0 else None),
            )
        )

    return StatsOut(total_signals=total, by_chain=by_chain)
