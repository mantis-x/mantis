"""Declarative base — mirrors packages/shared/src/db/models/base.py (see that package's note on why this is a copy, not a shared import)."""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
