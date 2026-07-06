"""
Database connection — mirrors packages/shared/src/db/connection.py.

This is a deliberate copy, not a cross-package import: every package in
this repo runs its worker as its own process with its own top-level `src`
namespace (see worker.py's sys.path.insert pattern in every package). Two
packages' `src` trees cannot both be imported as `src.*` in the same
process — whichever was inserted into sys.path first wins, and the second
import silently resolves against the wrong package. Keep this in sync with
packages/shared/src/db/connection.py if it changes.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_engine = None
_SessionLocal = None


def get_engine():
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
    global _engine, _SessionLocal
    _engine = None
    _SessionLocal = None
