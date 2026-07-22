#!/usr/bin/env bash
# Mantis Scout gate-watch — local, on-demand operational check.
#
# Reports the three metrics that tell us whether the Scout delivery gate is
# closing on real traffic (see the 2026-07-21/22 multi-wallet + threshold work):
#   1. subscriber delivery state (alerts_today / last_alert_date)
#   2. signals detected in the last 24h
#   3. count of multi-wallet (n_wallets>=2) signals ever — the fix-(a) payoff
#
# No secrets are stored in this file. It reads the Railway project token from the
# environment and resolves the DB public URL dynamically (so it survives a DB
# password rotation). Requires: railway CLI, and psql (macOS: brew install libpq).
#
# Usage:
#   RAILWAY_TOKEN=<grand-sparkle project token> bash scripts/gate_check.sh
set -euo pipefail

# macOS libpq (psql) is keg-only; add it to PATH if present.
[ -d /usr/local/opt/libpq/bin ] && export PATH="/usr/local/opt/libpq/bin:$PATH"

: "${RAILWAY_TOKEN:?set RAILWAY_TOKEN to the grand-sparkle project token}"

PGURL=$(railway variables --service Postgres --kv 2>/dev/null | grep '^DATABASE_PUBLIC_URL=' | cut -d= -f2-)
if [ -z "${PGURL:-}" ]; then
  echo "ERROR: could not resolve DATABASE_PUBLIC_URL via RAILWAY_TOKEN" >&2
  exit 1
fi

echo "==================================================================="
echo " Mantis Scout — gate watch   $(date -u +'%Y-%m-%d %H:%M UTC')"
echo "==================================================================="

echo; echo "[1] Subscriber delivery state (alerts_today resets at UTC midnight):"
psql "$PGURL" -P pager=off -c \
"select channel, recipient_id, min_confidence, is_pro, alerts_today, last_alert_date from subscriptions;"

echo "[2] New signals in the last 24h:"
psql "$PGURL" -P pager=off -c \
"select id, chain, confidence, signal_type,
        jsonb_array_length(wallets) as n_wallets,
        round(total_volume_usd::numeric,0) as usd,
        round(z_score::numeric,2) as z, detected_at
   from signals
  where detected_at >= now() - interval '24 hours'
  order by detected_at desc;"

echo "[3] Multi-wallet signals ever (n_wallets>=2) — the fix-(a) payoff:"
psql "$PGURL" -P pager=off -c \
"select count(*) as multiwallet_signals_all_time,
        max(detected_at) as most_recent
   from signals where jsonb_array_length(wallets) >= 2;"

echo; echo "Done."
