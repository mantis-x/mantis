"""
Mint an API-tier key (Phase A onboarding — docs/api_tier_build_plan.md A5).

Creates a standalone api_customers row and an api_keys row, then prints the
plaintext key EXACTLY ONCE. The plaintext is never stored or logged — only its
sha256 hash + a display prefix are persisted, so this output is the only chance
to copy it.

Usage:
    python scripts/mint_api_key.py --label "Acme Capital" [--contact ops@acme.xyz] \
        [--key-label "prod"] [--rate-limit 120] [--expires-days 30]

--expires-days omitted → NULL expiry (a comped pilot / design-partner key).
Requires DATABASE_URL (or the default local Postgres).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "shared"))

from src.db.connection import get_session
from src.db.models.api_customer import ApiCustomerRow
from src.db.models.api_key import ApiKeyRow, DEFAULT_RATE_LIMIT_PER_MIN
from src.security.api_keys import generate_api_key


def main() -> None:
    p = argparse.ArgumentParser(description="Mint an API-tier key")
    p.add_argument("--label", required=True, help="customer display name")
    p.add_argument("--contact", default=None, help="email/handle (optional)")
    p.add_argument("--key-label", default=None, help="label for this key (optional)")
    p.add_argument("--rate-limit", type=int, default=DEFAULT_RATE_LIMIT_PER_MIN,
                   help=f"requests/min (default {DEFAULT_RATE_LIMIT_PER_MIN})")
    p.add_argument("--expires-days", type=int, default=None,
                   help="tier length in days; omit for a comped (never-expiring) key")
    args = p.parse_args()

    plaintext, key_hash, key_prefix = generate_api_key()

    expires_at = None
    if args.expires_days is not None:
        expires_at = datetime.now(tz=timezone.utc) + timedelta(days=args.expires_days)

    with get_session() as session:
        customer = ApiCustomerRow(
            label=args.label, contact=args.contact, api_tier_expires_at=expires_at
        )
        session.add(customer)
        session.flush()   # get customer.id

        key = ApiKeyRow(
            customer_id=customer.id,
            key_hash=key_hash,
            key_prefix=key_prefix,
            label=args.key_label,
            rate_limit_per_min=args.rate_limit,
        )
        session.add(key)
        session.flush()
        customer_id, key_id = customer.id, key.id

    print("\n  API key minted — copy it now, it will NOT be shown again:\n")
    print(f"    {plaintext}\n")
    print(f"  customer_id={customer_id}  key_id={key_id}  prefix={key_prefix}")
    print(f"  rate_limit={args.rate_limit}/min  "
          f"expires={'never (comped)' if expires_at is None else expires_at.isoformat()}\n")


if __name__ == "__main__":
    main()
