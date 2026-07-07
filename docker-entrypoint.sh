#!/bin/sh
# Entrypoint for the deployed container: wait for Postgres, run migrations,
# then hand off to supervisord. Without this, AgentRegistry and
# SubscriptionManager (both Postgres-backed as of this build) crash on
# first use if the schema doesn't exist yet — this makes "deploy" always
# include "migrate", so that can't be forgotten.
set -e

echo "[entrypoint] Waiting for database..."
python3 - <<'PYEOF'
import os
import sys
import time

import psycopg2

url = os.environ.get("DATABASE_URL")
if not url:
    print("[entrypoint] DATABASE_URL not set — skipping DB wait", flush=True)
    sys.exit(0)

for attempt in range(1, 31):
    try:
        conn = psycopg2.connect(url, connect_timeout=5)
        conn.close()
        print("[entrypoint] Database reachable.", flush=True)
        sys.exit(0)
    except Exception as exc:
        print(f"[entrypoint] DB not ready (attempt {attempt}/30): {exc}", flush=True)
        time.sleep(2)

print("[entrypoint] Database never became reachable after 60s — "
      "continuing anyway; migration below will fail loudly if still down.", flush=True)
PYEOF

echo "[entrypoint] Running database migrations..."
cd /app/packages/shared
alembic upgrade head

echo "[entrypoint] Starting supervisord..."
cd /app
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
