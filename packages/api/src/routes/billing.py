"""
Billing — self-serve API-tier payment (Phase C). Authed + per-customer.

A customer registers the Arbitrum wallet they'll pay from (POST /v1/billing/wallet),
then sends USDC to the receive address shown by GET /v1/billing; the tracking
worker's ApiPaymentWatcher credits api_tier_expires_at on a confirmed transfer.
Read-only on our side — we never hold a key for the receive address.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, status

from src.auth import ApiKeyContext, require_api_key
from src.billing_pricing import payments_enabled, pricing_tiers, receive_address
from src.db.connection import get_session
from src.db.models.api_customer import ApiCustomerRow
from src.schemas import BillingInfoOut, PricingTierOut, WalletRegisterIn

router = APIRouter()

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


@router.get("/v1/billing", response_model=BillingInfoOut, tags=["billing"])
def billing_info(ctx: ApiKeyContext = Depends(require_api_key)) -> BillingInfoOut:
    with get_session() as session:
        customer = session.get(ApiCustomerRow, ctx.customer_id)
        wallet = customer.registered_wallet if customer else None
        expires = (
            customer.api_tier_expires_at.isoformat()
            if customer and customer.api_tier_expires_at else None
        )
        active = customer.is_active() if customer else False

    addr = receive_address()
    return BillingInfoOut(
        payments_enabled=payments_enabled(),
        receive_address=(addr or None),
        tiers=[PricingTierOut(**t) for t in pricing_tiers()],
        registered_wallet=wallet,
        api_tier_expires_at=expires,
        active=active,
    )


@router.post("/v1/billing/wallet", response_model=BillingInfoOut, tags=["billing"])
def register_wallet(
    body: WalletRegisterIn,
    ctx: ApiKeyContext = Depends(require_api_key),
) -> BillingInfoOut:
    if not _ADDRESS_RE.match(body.address):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="address must be a 0x-prefixed 40-hex-char wallet")
    with get_session() as session:
        customer = session.get(ApiCustomerRow, ctx.customer_id)
        if customer is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="customer not found")
        customer.registered_wallet = body.address.lower()   # match watcher's lowercased sender
    return billing_info(ctx)