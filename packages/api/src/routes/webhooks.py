"""
Webhook management — register/list/delete/test (API tier, Phase B).

Every route is authed (require_api_key) AND scoped to the caller's customer_id:
list/get/delete/test only ever touch the caller's own webhooks, so one customer
can never see or mutate another's (IDOR). Registration and the test endpoint
run the SSRF guard on the URL (see url_guard.py).
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Response, status

from src.auth import ApiKeyContext, require_api_key
from src.db.connection import get_session
from src.db.models.webhook import WebhookRow
from src.schemas import (
    WebhookCreatedOut,
    WebhookCreateIn,
    WebhookOut,
    WebhookTestResultOut,
)
from src.security.webhook_signing import SIG_HEADER, TS_HEADER, generate_webhook_secret, sign
from src.url_guard import UnsafeWebhookURL, validate_webhook_url

router = APIRouter()


def _to_out(wh: WebhookRow) -> WebhookOut:
    return WebhookOut(
        id=wh.id,
        url=wh.url,
        active=wh.active,
        disabled=wh.disabled_at is not None,
        event_filters=wh.event_filters,
        consecutive_failures=wh.consecutive_failures,
        created_at=wh.created_at.isoformat(),
    )


@router.post("/v1/webhooks", response_model=WebhookCreatedOut, status_code=status.HTTP_201_CREATED,
             tags=["webhooks"])
def create_webhook(
    body: WebhookCreateIn,
    ctx: ApiKeyContext = Depends(require_api_key),
) -> WebhookCreatedOut:
    try:
        validate_webhook_url(body.url)
    except UnsafeWebhookURL as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    secret = generate_webhook_secret()
    filters = body.event_filters.model_dump(exclude_none=True) if body.event_filters else None
    with get_session() as session:
        wh = WebhookRow(
            customer_id=ctx.customer_id, url=body.url, secret=secret,
            event_filters=(filters or None),
        )
        session.add(wh)
        session.flush()
        out = _to_out(wh)
    return WebhookCreatedOut(**out.model_dump(), secret=secret)


@router.get("/v1/webhooks", response_model=list[WebhookOut], tags=["webhooks"])
def list_webhooks(ctx: ApiKeyContext = Depends(require_api_key)) -> list[WebhookOut]:
    with get_session() as session:
        rows = (
            session.query(WebhookRow)
            .filter(WebhookRow.customer_id == ctx.customer_id)
            .order_by(WebhookRow.id.desc())
            .all()
        )
        return [_to_out(w) for w in rows]


def _owned_or_404(session, webhook_id: int, customer_id: int) -> WebhookRow:
    wh = (
        session.query(WebhookRow)
        .filter(WebhookRow.id == webhook_id, WebhookRow.customer_id == customer_id)
        .first()
    )
    if wh is None:
        # Same 404 whether it doesn't exist or belongs to someone else — don't leak existence.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")
    return wh


@router.delete("/v1/webhooks/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["webhooks"])
def delete_webhook(webhook_id: int, ctx: ApiKeyContext = Depends(require_api_key)):
    with get_session() as session:
        wh = _owned_or_404(session, webhook_id, ctx.customer_id)
        session.delete(wh)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/v1/webhooks/{webhook_id}/test", response_model=WebhookTestResultOut, tags=["webhooks"])
def test_webhook(webhook_id: int, ctx: ApiKeyContext = Depends(require_api_key)) -> WebhookTestResultOut:
    with get_session() as session:
        wh = _owned_or_404(session, webhook_id, ctx.customer_id)
        url, secret = wh.url, wh.secret

    # Re-validate at send time (DNS may have changed since registration).
    try:
        validate_webhook_url(url)
    except UnsafeWebhookURL as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    body = json.dumps({"type": "test", "webhook_id": webhook_id, "message": "Mantis webhook test"},
                      sort_keys=True)
    ts, sigval = sign(secret, body)
    headers = {"Content-Type": "application/json", TS_HEADER: ts, SIG_HEADER: sigval,
               "X-Mantis-Signal-Id": "test"}
    try:
        import requests
        resp = requests.post(url, data=body, headers=headers, timeout=10)
        return WebhookTestResultOut(delivered=200 <= resp.status_code < 300, status_code=resp.status_code)
    except Exception:  # noqa: BLE001
        return WebhookTestResultOut(delivered=False, status_code=None)
