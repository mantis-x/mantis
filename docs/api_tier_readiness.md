# Mantis API Tier — Build Readiness & Plan

_Planning doc for the advertised **API tier ($299/mo — institutional feed + webhooks)** from the
business model. Mirrors the structure of `docs/execute_readiness.md`. As of this writing the API
tier is **not built** (PROJECT_STATE.md #35 flagged it as a separate, larger scope). This doc is
the plan, not an implementation — nothing here ships until the decisions in §3/§7 are made and the
§8 flip checklist is green._

_Written 2026-07-24._

## 1. What the product is

Two distinct deliverables sold together under one $299/mo tier:

- **A. Institutional feed (pull REST API)** — authenticated HTTP endpoints over the durable
  `signals` table: list/filter recent signals, fetch one by id (with its PnL/hit-rate outcomes),
  aggregate track-record stats. Read-only. Lowest risk, fastest value.
- **B. Webhooks (push)** — a customer registers endpoint URL(s) and receives each new signal as an
  HMAC-signed HTTP POST in near-real-time, with retries/backoff and a delivery log. Higher scope
  (a new outbound-delivery subsystem and its own failure modes).

Pay-for-a-period crypto billing (USDC on Arbitrum), same model as Pro tier, at the $299/mo price
point with the existing 6mo/12mo discount structure.

## 2. What already exists that we build on

- **Durable read source is ready.** The tracking worker persists **every enriched signal** to the
  Postgres `signals` table (`packages/shared/src/tracking/signal_outcome_tracker.py:persist_signal`,
  consumed from `mantis:signals:tracking`). This is the query source — **not** the Redis lists,
  which are capped (`ltrim` to 999/4999) and useless for historical queries.
  - Persisted columns (`packages/shared/src/db/models/signal.py`): chain, protocol, pool_address,
    wallets, signal_type, confidence, summary, key_factors, detected_at, deliver_at, z_score,
    total_volume_usd, event_type, audit_tx_hash, + an `outcomes` relationship to `signal_outcomes`.
  - **`signal_outcomes` (PnL / hit-rate) is durably persisted** and joinable — this is the real
    institutional value-add (a raw whale feed is commoditised; a *scored, back-tested* feed is not).
  - **Gap found 2026-07-24:** `smart_money_count` (added #28) is **NOT** persisted — it exists only
    in the Redis payload and the Telegram card, never written to `SignalRow`. If we want to sell it
    via the API, it needs a column + a one-line add in `persist_signal` (small migration).
- **An aiohttp HTTP-server pattern already exists** (`packages/delivery/src/line/bot.py` — `web.Application`,
  `AppRunner`, `TCPSite`), so serving HTTP in this stack is already-trodden ground.
- **Crypto billing is live and generalisable.** `packages/shared/src/billing/pro_payment_watcher.py`
  already does pay-for-period, amount→tier matching, early-payment stacking, idempotency, expiry
  sweep, reorg-safe confirmations. An API tier reuses this almost verbatim (see §3 Phase C / §7).
- **The "build inert behind a flag" pattern is established** (`BYREAL_DRY_RUN`,
  `PRO_TIER_PAYMENTS_ENABLED`). API tier follows it: `API_TIER_ENABLED=false` by default.
- **A public Railway domain now exists** (`mantis-production-6b6a.up.railway.app`, created 2026-07-24).
  The API needs a public ingress; this is it (or a custom domain later).

## 3. THE decision that shapes everything: what is an API customer?

Pro tier is bolted onto a `subscriptions` row (`channel` + `recipient_id`, e.g. Telegram chat id).
**An institutional API customer likely has no Telegram/Discord/LINE presence at all.** Key
ownership, billing credit, expiry sweep, and rate-limit accounting all inherit from this choice, so
it must be made before any schema is written:

- **Option A — standalone entity (recommended).** New `api_customers` + `api_keys` tables with their
  own lifecycle, independent of `subscriptions`. Cleaner separation; no forcing a chat-channel
  identity onto a headless customer. Billing credit sets `api_tier_expires_at` on the customer row.
- **Option B — extend `subscriptions`.** Add an `api_tier_expires_at` column and a synthetic
  `channel="api"` row. Less new schema, but overloads a table designed around chat channels and
  makes "a customer with no chat channel" awkward.

**DECIDED 2026-07-24 — Option A (standalone `api_customers` + `api_keys`).** An API customer is a
first-class headless entity, not a `subscriptions` row. All schema below follows this.

## 4. Prerequisites — code (phased, all behind `API_TIER_ENABLED`)

### Phase A — Read-only feed (lowest risk)
- [ ] **API key management** — `api_keys` table: `key_hash` (store a hash, **never** plaintext —
      show the key once on creation), `key_prefix` (for display/identification), label, owner (per §3),
      tier, created_at, last_used_at, revoked_at, rate_limit. Auth via `Authorization: Bearer <key>`.
- [ ] **New `api` worker** — a dedicated supervisord program (own process/port), exposed via the
      Railway domain. Do **not** bolt onto the delivery worker's aiohttp server (that only starts
      with LINE creds and couples the API to delivery). Framework: **FastAPI** (auto-generated
      OpenAPI/Swagger docs — a real selling point for a dev-facing product) or aiohttp (already a
      dependency, lower ergonomics, no auto-docs). If FastAPI: add it to **both** the root and
      package `requirements.txt` (the #36 root-vs-package incident).
- [ ] **Endpoints (v1)**: `GET /v1/signals` (filters: chain, signal_type, min_confidence, since,
      cursor, limit; sorted detected_at desc, cursor pagination since the table appends live),
      `GET /v1/signals/{id}` (+ its outcomes), `GET /v1/stats` (aggregate hit-rate), `GET /v1/health`
      (unauthenticated liveness).
- [ ] **Rate limiting** — per-key, Redis-backed (already present), 429 + `Retry-After`.
- [ ] Hand-mintable keys at first (a script), so a pilot customer can be onboarded before billing exists.

### Phase B — Webhooks (push)
- [ ] `webhooks` table (owner, url, secret, active, event_filters, failure_count, disabled_at) +
      `webhook_deliveries` table (audit/retry: webhook_id, signal_id, status, attempts, response_code,
      next_retry_at).
- [ ] **4th enrichment fan-out** — add `rpush mantis:signals:api` alongside the existing three
      (`packages/enrichment/src/worker.py`), matching the existing pattern exactly.
- [ ] **Webhook dispatcher** — consumes `mantis:signals:api`, loads active matching webhooks, POSTs
      with `X-Mantis-Signature: sha256=<hmac>` + a timestamp header (replay protection, Stripe/GitHub
      convention), a delivery id for recipient-side idempotency, exponential-backoff retries, and
      auto-disable after N consecutive failures (reuse the `EnrichmentAlerter` streak pattern).
- [ ] **Webhook management endpoints** — register/list/delete/test; publish signature-verification
      sample code for customers.

### Phase C — Self-serve billing
- [ ] **API-tier price points** — $299/mo + 6mo/12mo discounts, computed live from an env base like
      Pro's (`packages/shared/src/billing/pro_payment_watcher.py`).
- [ ] **Product distinction** (open decision, §7): the current `_match_tier` only knows Pro amounts —
      a $299 transfer would be recorded **unmatched** today. Distinguishing an API payment on-chain
      needs either a separate `API_TIER_RECEIVE_ADDRESS` (cleanest — reuse the watcher parametrised
      by product) or a pre-declared intent keyed to the registered wallet.
- [ ] On credit: set `api_tier_expires_at` + activate the customer's API key; expiry sweep
      deactivates it (mirror `sweep_expired_pro`).

## 5. Prerequisites — ops & security (a NEW public attack surface)

- [ ] **Security review** of the public API + auth + webhook signing — non-negotiable, same gate as
      Execute. Key hashing (store hash, show once), no keys in logs, TLS (Railway-provided).
- [ ] **Abuse protection** beyond rate limits — key rotation UX, per-key revocation, anomaly watch.
- [ ] **SLA / uptime tension — name it explicitly.** A paid $299 institutional API creates
      availability expectations that **directly re-open the paid-RPC decision deferred 2026-07-24**
      (see PROJECT_STATE.md / memory: paid RPC key was "only before scale-up"). **API go-live IS the
      scale-up trigger.** Also implies real monitoring/alerting on the API worker and ingestion
      freshness, and likely a paid RPC to keep the feed current under load.
- [ ] **Load testing** the read endpoints against the production `signals` table before customers hit it.

## 6. Prerequisites — legal / business (Phase 3, user-owned — not code)

Same §6 gate as Pro/Execute, plus:
- [ ] **Data licensing / ToS for institutional customers** — redistribution terms, "not financial
      advice," no-warranty, and any **SLA/uptime contract terms** a $299 customer will expect.
- [ ] Entity / tax / regulatory posture for institutional revenue (already flagged for Pro/Execute).

## 7. Open decisions — need the user before building

1. ~~**Customer identity model** (§3)~~ **DECIDED 2026-07-24 — standalone `api_customers` + `api_keys`.**
2. **Billing product distinction** (§4 Phase C) — separate receive address (recommended) vs.
   pre-declared wallet intent. _(needed for Phase C, not Phase A)_
3. **Does the feed expose sub-threshold signals?** `mantis:signals:tracking` persists *every*
   enriched signal, including ones below the delivery/subscriber confidence threshold — more than
   Telegram subscribers ever see. For an institutional *raw* feed that may be a feature (they do
   their own filtering); for a curated feed, mirror the delivery threshold. Product call, not a default.
4. **API framework** — FastAPI (auto-OpenAPI docs, dev-facing polish, +1 dependency) vs. aiohttp
   (already in-stack, no auto-docs).
5. **Sell `smart_money_count`?** If yes, add the missing column + persist it (small migration, §2).

## 8. Flip checklist — before `API_TIER_ENABLED=true`

1. [ ] §3 customer-identity model decided and schema built.
2. [ ] Phase A feed live behind the flag; keys hand-mintable; pilot customer validated on a real key.
3. [ ] Rate limiting + auth tested (including revocation).
4. [ ] (If selling webhooks) Phase B dispatcher live; HMAC verified end-to-end against a real
       external endpoint; retry/auto-disable tested.
5. [ ] Security review of the public surface completed and signed off.
6. [ ] Billing (Phase C) tested end-to-end against a real on-chain payment (same discipline as the
       Pro-tier real-payment verification, PROJECT_STATE.md #35).
7. [ ] Paid RPC + monitoring in place (the SLA/uptime prerequisite, §5).
8. [ ] Legal/ToS/data-licensing gate (§6) cleared by the account holder.

## 9. Recommended sequence

1. Decide §7 #1 (customer identity) — blocks everything.
2. Build **Phase A** (read feed) behind `API_TIER_ENABLED`, hand-mint a key, onboard a design-partner
   / pilot on the hand-issued key. Real usage before billing exists — cheapest path to validation and
   revenue conversations.
3. Build **Phase C** (billing) once a pilot proves the feed's value — self-serve $299 onboarding.
4. Build **Phase B** (webhooks) — the higher-effort, higher-support-burden piece; add once there's
   demonstrated demand for push over pull.
5. Resolve §5 (security review, paid RPC/SLA) and §6 (legal) in parallel — these are the real long
   poles, same as Execute, not the code.
