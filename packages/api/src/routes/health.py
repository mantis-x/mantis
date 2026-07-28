"""Unauthenticated liveness — stays up even when API_TIER_ENABLED is false, so
uptime monitoring works before the tier is sold."""
from __future__ import annotations

from fastapi import APIRouter

from src.config import api_tier_enabled
from src.schemas import HealthOut

router = APIRouter()


@router.get("/v1/health", response_model=HealthOut, tags=["meta"])
def health() -> HealthOut:
    return HealthOut(status="ok", api_tier_enabled=api_tier_enabled())
