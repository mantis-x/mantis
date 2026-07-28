"""
Database connection — a single SQLAlchemy engine + session factory shared
by every package that persists to Postgres: executor's agents, delivery's
subscriptions, and the signal/signal_outcome/execution tables that back
the track record / backtest instrumentation.

Usage:

    from src.db.connection import get_session
    with get_session() as session:
        session.add(some_row)
        # commits automatically on clean exit, rolls back on exception
"""
from __future__ import annotations

import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_engine = None
_SessionLocal = None


def get_engine():
    """Lazily create the module-level engine from the DATABASE_URL env var."""
    global _engine
    if _engine is None:
        database_url = os.getenv(
            "DATABASE_URL", "postgresql://mantis:password@localhost:5432/mantis"
        )
        _engine = create_engine(database_url, pool_pre_ping=True, future=True)
    return _engine


def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


@contextmanager
def get_session():
    """Context manager yielding a session; commits on success, rolls back on error."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine_for_tests() -> None:
    """Drop the cached engine/session factory so tests can point at a fresh DATABASE_URL."""
    global _engine, _SessionLocal
    _engine = None
    _SessionLocal = None
