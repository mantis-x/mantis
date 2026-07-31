"""
List API-tier customers and their keys (admin view).

Usage:  python scripts/list_api_keys.py [--active] [--customer-id N]
  --active       only customers whose tier is currently active
  --customer-id  only this customer

Needs DATABASE_URL pointing at the target DB (see scripts/mint_api_key.py for the
prod-connection note — use the Postgres *public* URL from your laptop).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "shared"))

from src.db.connection import get_session
from src.db.models.api_customer import ApiCustomerRow
from src.db.models.api_key import ApiKeyRow


def _expiry_str(c: ApiCustomerRow, now: datetime) -> str:
    if c.api_tier_expires_at is None:
        return "comped (never expires)"
    return c.api_tier_expires_at.isoformat() + ("" if c.api_tier_expires_at > now else "  ⚠️ EXPIRED")


def main() -> None:
    p = argparse.ArgumentParser(description="List API customers + keys")
    p.add_argument("--active", action="store_true", help="only currently-active customers")
    p.add_argument("--customer-id", type=int, default=None)
    args = p.parse_args()

    now = datetime.now(tz=timezone.utc)
    with get_session() as s:
        q = s.query(ApiCustomerRow).order_by(ApiCustomerRow.id.asc())
        if args.customer_id is not None:
            q = q.filter(ApiCustomerRow.id == args.customer_id)
        customers = q.all()
        if args.active:
            customers = [c for c in customers if c.is_active(now)]

        if not customers:
            print("No matching API customers.")
            return

        for c in customers:
            status = "ACTIVE" if c.is_active(now) else "inactive"
            print(f"\n#{c.id}  {c.label}  [{status}]")
            print(f"     contact: {c.contact or '—'}")
            print(f"     tier:    {_expiry_str(c, now)}")
            print(f"     wallet:  {c.registered_wallet or '—'}")
            keys = s.query(ApiKeyRow).filter(ApiKeyRow.customer_id == c.id).order_by(ApiKeyRow.id.asc()).all()
            if not keys:
                print("     keys:    (none)")
            for k in keys:
                st = "REVOKED" if k.is_revoked else "live"
                last = k.last_used_at.isoformat() if k.last_used_at else "never"
                print(f"       key #{k.id}  {k.key_prefix}…  [{st}]  rate={k.rate_limit_per_min}/min  last_used={last}")


if __name__ == "__main__":
    main()
