"""
Revoke (or un-revoke) API keys. A revoked key 401s immediately on its next call.

Usage:
    python scripts/revoke_api_key.py --prefix mantis_live_XXXXXXXX
    python scripts/revoke_api_key.py --key-id 3
    python scripts/revoke_api_key.py --customer-id 5        # revokes ALL of a customer's keys
    python scripts/revoke_api_key.py --key-id 3 --unrevoke  # reactivate a key

Exactly one selector (--prefix / --key-id / --customer-id) is required.
Needs DATABASE_URL pointing at the target DB (see scripts/mint_api_key.py note).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "shared"))

from src.db.connection import get_session
from src.db.models.api_key import ApiKeyRow


def main() -> None:
    p = argparse.ArgumentParser(description="Revoke / un-revoke API keys")
    sel = p.add_mutually_exclusive_group(required=True)
    sel.add_argument("--prefix", help="key_prefix (e.g. mantis_live_JpzyAWXm)")
    sel.add_argument("--key-id", type=int)
    sel.add_argument("--customer-id", type=int, help="revoke ALL keys for this customer")
    p.add_argument("--unrevoke", action="store_true", help="clear revoked_at instead of setting it")
    args = p.parse_args()

    with get_session() as s:
        q = s.query(ApiKeyRow)
        if args.prefix is not None:
            q = q.filter(ApiKeyRow.key_prefix == args.prefix)
        elif args.key_id is not None:
            q = q.filter(ApiKeyRow.id == args.key_id)
        else:
            q = q.filter(ApiKeyRow.customer_id == args.customer_id)

        keys = q.all()
        if not keys:
            print("No matching keys.")
            return

        new_val = None if args.unrevoke else datetime.now(tz=timezone.utc)
        verb = "Un-revoked" if args.unrevoke else "Revoked"
        for k in keys:
            k.revoked_at = new_val
            print(f"{verb}: key #{k.id}  {k.key_prefix}…  (customer {k.customer_id})")
        print(f"\n{verb} {len(keys)} key(s).")


if __name__ == "__main__":
    main()
