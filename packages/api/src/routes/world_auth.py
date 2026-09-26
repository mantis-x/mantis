"""World ID Agents OIDC login and callback endpoints."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import RedirectResponse

from src.world_oidc import WorldOIDCError, authorization_url, exchange_and_issue_artifact

log = logging.getLogger("mantis.api.world_auth")
router = APIRouter(prefix="/auth/world", tags=["world-id"])


@router.get("/login")
def world_login(signal_hash: str = Query(..., min_length=10, max_length=128)) -> RedirectResponse:
    """Start a World OIDC approval for one canonical trade-intent hash."""
    try:
        return RedirectResponse(authorization_url(signal_hash), status_code=status.HTTP_302_FOUND)
    except Exception as exc:
        log.warning("World login unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="World ID is not configured") from exc


@router.get("/callback")
def world_callback(code: str = Query(...), state: str = Query(...)) -> dict:
    """Exchange the code and return a short-lived server-signed approval artifact."""
    try:
        return {"ok": True, **exchange_and_issue_artifact(code, state)}
    except WorldOIDCError as exc:
        log.warning("World callback rejected: %s", exc)
        raise HTTPException(status_code=400, detail="World ID approval could not be validated") from exc
    except Exception as exc:
        log.exception("World callback failed")
        raise HTTPException(status_code=502, detail="World ID service error") from exc
