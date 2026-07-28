# Mantis API Tier — Phase A Build Plan (implementation-level)

_The step-by-step build plan for **Phase A (read feed)** — the task breakdown to go from
"planned" to "a pilot customer can curl a real endpoint." Companion to `docs/api_tier_readiness.md`
(which holds the product scope, decisions, and go-live gate). Phases B/C outlined at the end.
Nothing here is built yet — this is the plan, written 2026-07-24._

## Assumptions locked in
- **Customer identity: standalone** `api_customers` + `api_keys` (decided 2026-07-24).
- **Read source: the durable Postgres `signals` table** (not Redis lists). No new ingestion work.
- **Everything gated behind `API_TIER_ENABLED=false`** — inert until flipped, same pattern as
  `BYREAL_DRY_RUN` / `PRO_TIER_PAYMENTS_ENABLED`.
- **Framework: FastAPI (assumed default)** — for the auto-generated OpenAPI/Swagger docs, a real
  selling point for a paid dev API. Still flippable to aiohttp (already in-stack); if changed, only
  tasks A2/A4's server glue differ, not the schema/auth/query logic. _(open decision, readiness §7 #4)_

## Deployment shape (important — decide before A2)
Today the repo runs **one** Railway service: a single container, `supervisord` running 6 workers.
The public domain (`mantis-production-6b6a.up.railway.app`) routes to **one target port** on that
service. So the `api` worker runs as a **7th supervisord program in the same container**, binding
the Railway-injected `$PORT`; the domain's target port is set to match. (Alternative: a *second*
Railway service sharing the same Postgres/Redis — cleaner isolation and independent scaling, but
more infra. Recommend same-container for Phase A, revisit if load demands it.)

---

## Phase A — STATUS: built & locally verified 2026-07-28 (not yet deployed)

Tasks A1–A6 are implemented and verified locally; A7 (canary→promote deploy) is
the only Phase-A item still pending. Verification done (against a throwaway local
Postgres 16 + fakeredis, Docker being unavailable):
- Migration `0003` round-trip up→down→up clean; DDL renders correct Postgres.
- 15/15 api tests pass (key-gen unit + auth/gate/feed/pagination/stats/rate-limit
  integration on real Postgres). Shared suite still 51/51 (additive change, no regression).
- Real CLI mint → minted key authenticates against the live FastAPI app; `/v1/health`
  200, tier-disabled 404, no-key 401, rate-limit 429, OpenAPI serves all 4 paths.
- Framework: **FastAPI** (chosen — auto OpenAPI at `/docs`). Everything behind
  `API_TIER_ENABLED=false`. A6 docs = auto OpenAPI for now (standalone `docs/api.md` optional).

## Phase A — task breakdown (ordered; each task is independently testable)

### A1 — Schema & keys  (`packages/shared`)
- [ ] `db/models/api_customer.py` — `ApiCustomerRow`: id, label, contact (nullable),
      `api_tier_expires_at` (nullable = never/pilot), created_at.
- [ ] `db/models/api_key.py` — `ApiKeyRow`: id, `customer_id` (FK), `key_hash` (sha256 of the
      plaintext — **store the hash only**), `key_prefix` (first ~8 chars, for display/lookup speed),
      label, `rate_limit_per_min`, created_at, last_used_at (nullable), revoked_at (nullable).
- [ ] `security/api_keys.py` — `generate_key()` → returns `(plaintext, hash, prefix)` with format
      `mantis_live_<32+ url-safe random>`; `hash_key(plaintext)`; constant-time compare on lookup.
- [ ] Alembic migration `0003_api_tier.py` (both tables; verify up→inspect→down→up on a disposable
      Postgres, same discipline as migration `0002`).
- **Done when:** migration round-trips clean; a unit test creates a customer+key and re-hashes the
  plaintext to the stored hash.

### A2 — The `api` worker  (`packages/api`, new)
- [ ] `packages/api/src/main.py` — FastAPI app factory + `uvicorn` runner binding `0.0.0.0:$PORT`.
- [ ] `packages/api/requirements.txt` **and** the **root** `requirements.txt` (both — the #36
      root-vs-package deploy incident: prod installs from root only).
- [ ] `supervisord.conf` — add `[program:api]` (the 7th worker; `autorestart=true` like the rest).
- [ ] Railway: point the public domain's **target port** at the api worker's port.
- [ ] `GET /v1/health` (unauthenticated) returning `{status, git_sha?}`.
- **Done when:** `curl https://<domain>/v1/health` returns 200 in production (behind the flag,
  health can stay always-on); `supervisord` shows `api` RUNNING alongside the other 6.

### A3 — Auth + rate limiting  (`packages/api/src/auth.py`)
- [ ] FastAPI dependency: extract `Authorization: Bearer <key>`, hash, look up by prefix→verify hash,
      reject if missing/revoked/expired (`api_tier_expires_at` in the past); bump `last_used_at`.
- [ ] `API_TIER_ENABLED` gate — when false, authenticated routes return 404 (don't advertise the
      surface); `/v1/health` stays up.
- [ ] Redis-backed per-key rate limiter (token bucket / sliding window; Redis already present) →
      429 + `Retry-After` on exhaustion.
- **Done when:** tests cover valid-key-passes, missing/revoked/expired-rejected, flag-off-hides-routes,
  and rate-limit-429. (Use fakeredis + a disposable Postgres, matching existing test patterns.)

### A4 — Endpoints  (`packages/api/src/routes/signals.py`)
- [ ] `GET /v1/signals` — filters: `chain`, `signal_type`, `min_confidence`, `since` (ISO or cursor),
      `limit` (capped, e.g. 100); **cursor pagination** on `id` desc (stable while the table appends).
      Query `SignalRow`; serialize via a Pydantic response model (reuse `SignalRow.to_dict` shape).
      **Sub-threshold exposure is a product decision** (readiness §7 #3): default to mirroring the
      delivery threshold unless we decide to sell the raw firehose.
- [ ] `GET /v1/signals/{id}` — one signal + its `outcomes` (the PnL/hit-rate — the real value-add).
- [ ] `GET /v1/stats` — aggregate hit-rate / count by chain from `signal_outcomes`.
- [ ] Decide re `smart_money_count` (readiness §7 #5): if selling it, add the column + persist it in
      `persist_signal` first (it's currently Redis/Telegram-only, never in Postgres). Otherwise omit.
- **Done when:** integration tests hit each endpoint against a seeded disposable Postgres — filters,
  pagination stability, auth enforced, 404 on missing id.

### A5 — Key minting + pilot onboarding
- [ ] `scripts/mint_api_key.py --label <name>` — creates a customer + key, prints the plaintext
      **once** (never stored/logged), sets `api_tier_expires_at` (NULL for a comped pilot).
- **Done when:** run it, curl `/v1/signals` with the printed key against production, get real rows.

### A6 — Customer-facing docs
- [ ] FastAPI auto-serves OpenAPI at `/docs` + `/openapi.json` — enough for a pilot. Add a one-page
      `docs/api.md` (auth header, endpoints, filters, an example request/response, rate limits).

### A7 — Deploy & verify (the usual canary→promote)
- [ ] `railway up` canary → confirm all 7 workers RUNNING, `/v1/health` 200, hand-minted key works
      on real data → merge to `main` → confirm auto-redeploy healthy. (Same rhythm as every deploy
      this project does.)

**Phase A exit:** a pilot/design-partner can authenticate and pull real, back-tested signals over
HTTP. No billing yet (comped key) — that's the point: prove value before building payment.

---

## Phase B — Webhooks (outline; build after a pilot wants push)
- Tables `webhooks` + `webhook_deliveries`; add a 4th enrichment fan-out `rpush mantis:signals:api`;
  a webhook dispatcher (HMAC-signed POSTs, replay-protection timestamp, exponential-backoff retries,
  auto-disable after N failures — reuse the `EnrichmentAlerter` streak pattern); register/list/delete/
  test endpoints + a signature-verification sample for customers.

## Phase C — Self-serve billing (outline; build once value is proven)
- API-tier price points ($299/mo + 6/12mo discounts) computed from an env base like Pro's; **product
  distinction** (readiness §7 #2) via a separate `API_TIER_RECEIVE_ADDRESS` (recommended — reuse
  `pro_payment_watcher` parametrised by product); on credit set `api_tier_expires_at` + activate the
  key; expiry sweep deactivates it (mirror `sweep_expired_pro`); verify end-to-end against one real
  on-chain payment (same discipline as PROJECT_STATE.md #35).

## Cross-cutting (from readiness §5/§6 — don't skip at go-live)
- Security review of the public surface (new attack surface — non-negotiable, like Execute).
- Paid RPC + monitoring: **API go-live is the scale-up trigger** that reopens the deferred paid-RPC
  decision (a $299 SLA implies uptime).
- Legal/ToS/data-licensing gate (user-owned).

## Rough effort
Phase A ≈ a few focused days (schema + worker + auth + 4 endpoints + tests + deploy). B and C are
each a comparable chunk on top. Multi-week feature overall; Phase A is deliberately the fastest path
to a paying pilot.
