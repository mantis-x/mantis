"""
List API access-request leads captured by POST /v1/access-request (stored in
Redis by packages/api/src/routes/access.py). The Telegram ping is the primary
notification; this is the durable backup / bulk view.

Usage:  python scripts/list_access_requests.py [--limit N]
Requires REDIS_URL (defaults to localhost).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os

import redis

_LEADS_KEY = "mantis:access_requests"


def main() -> None:
    p = argparse.ArgumentParser(description="List Mantis API access-request leads")
    p.add_argument("--limit", type=int, default=50)
    args = p.parse_args()

    r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
    raw = r.lrange(_LEADS_KEY, 0, args.limit - 1)  # newest first (LPUSH)
    if not raw:
        print("No access requests yet.")
        return

    print(f"{len(raw)} access request(s), newest first:\n")
    for item in raw:
        try:
            d = json.loads(item)
        except json.JSONDecodeError:
            print("  (unparseable)", item)
            continue
        when = dt.datetime.fromtimestamp(d.get("ts", 0), tz=dt.timezone.utc).isoformat()
        print(f"  {when}  {d.get('email','?'):32}  wallet={d.get('wallet') or '—'}  note={d.get('note') or '—'}")


if __name__ == "__main__":
    main()
