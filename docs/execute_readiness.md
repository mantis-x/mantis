# Execute — Live-Readiness Plan

_Planning document (2026-07-22). No code here flips real trading on. Execute is
**dry-run on every chain** today and must stay that way until the gate in §7 is
cleared. Written after the Scout delivery gate closed (multi-wallet signals now
firing + delivering), when Phase 2 became viable._

> **The one rule:** do **not** set `BYREAL_DRY_RUN=false` in production until
> every item in the §7 checklist is true. Flipping it moves real funds.

---

## 1. How Execute works today (grounded in the code, not memory)

Pipeline: enrichment fans a signal to `mantis:signals:exec` (a dedicated FIFO
Redis queue) → the **executor worker** (`packages/executor/src/worker.py`)
`blpop`s each signal → evaluates it against **registered agent intent rules**
(`intent/rule_engine.py`, `rule_parser.py`, `intent_classifier.py`) → if a rule
matches, runs **guards** → executes the swap → logs identity/decision on-chain
(`identity/erc8004_logger.py`).

- **Guards** (`guards/guard_runner.py`): `position_cap` (trade USD ≤
  `max_position` % of the **real** wallet balance), `slippage_check`,
  `blacklist`. Run before any swap.
- **Executors**:
  - **Arbitrum** — `arbitrum/swap_executor.py`: real Uniswap V3 SwapRouter02 +
    QuoterV2, live Chainlink ETH/USD pricing, slippage protection, gas-price
    buffering. **Pure Python/web3 — no external binary dependency.**
  - **Mantle** — `byreal/cli_runner.py`: subprocess wrapper around `byreal-cli`
    (an npm binary, `@byreal-io/byreal-cli`).
  - **Ethereum** — none. `_do_swap` raises for any chain without an executor
    (fixed 2026-07-18), so it fails loudly rather than misrouting.
- **Dry-run switch**: `BYREAL_DRY_RUN` env (default `true`). Currently `true` in
  production. It gates **both** executors.

## 2. Current readiness by chain

| Chain | Executor built | Blocker to live | Flip-ability |
|---|---|---|---|
| **Arbitrum** | ✅ Real (web3) | Dry-run flag only + gate §7 | **Highest** — no external dep |
| **Mantle** | ⚠️ Via `byreal-cli` | `byreal-cli` **not installed in the container** — stays mocked even if flag flips | Low until CLI packaged |
| **Ethereum** | ❌ None | Executor not built | N/A |

**Arbitrum is the only sensible first live chain.** Its path is self-contained
Python/web3, it already has the real guards, pricing, and slippage logic, and it
was the most exercised during build-out.

## 3. Prerequisites — Technical (code)

- [ ] **Token approvals / allowance handling** — confirm SwapRouter02 allowance
      flow is correct and minimal (approve exact amount, not infinite) before any
      live swap.
- [ ] **Real wallet-balance source verified live** — `_wallet_balance_usd` must
      return the true on-chain balance for the position-cap guard to mean
      anything (it raises for unknown chains today — good).
- [ ] **Hard per-trade and per-day notional caps** — independent of the % guard;
      an absolute USD ceiling (e.g. start at $10–50/trade) so a pricing bug can't
      size a huge order. Not present as an absolute cap today; add it.
- [ ] **Idempotency / double-execution guard** — ensure a signal replayed on the
      `:exec` queue (or a worker restart mid-trade) can't double-submit. Verify
      the FIFO consume + any dedup.
- [ ] **Kill switch** — a single env/flag (or Redis key) the worker checks each
      loop that halts all execution instantly without a redeploy.
- [ ] **Slippage guard is real, not the hackathon pass-through** —
      `guard_runner.py` notes the slippage check is a simplified pass; tighten to
      a real quote-vs-execution bound before live.
- [ ] **Mantle only:** package `byreal-cli` into the Docker image (or drop Mantle
      execution from v1 and go Arbitrum-only).

## 4. Prerequisites — Safety & operational

- [ ] **Start in shadow/paper on live rails** — run the real executor against
      real quotes with $0 or a $1 cap first, comparing intended vs simulated
      fills for N days before any nonzero size.
- [ ] **Alerting on every execution + every guard abort** — reuse the enrichment
      alerter pattern; an admin push on any live trade and any failure.
- [ ] **Per-execution on-chain audit confirmed** — `erc8004_logger` decision log
      lands on the right chain (already per-chain routed; verify live).
- [ ] **Monitoring dashboard** — realized PnL, fill quality, gas spend, guard
      trips. `signal_outcomes` already tracks signal PnL; execution needs its own.
- [ ] **Runbook** — how to halt, how to rotate keys, how to reconcile a stuck tx.

## 5. Prerequisites — Custody & security (the hard technical gate)

- [ ] **Private-key custody model decided** — the agent wallet key is currently
      an env var (`BYREAL_PRIVATE_KEY` / deployer key). For real funds this needs
      a deliberate custody decision: KMS/HSM, a dedicated hot wallet with strict
      caps, and **no key in plaintext env**. This is the single biggest technical
      risk.
- [ ] **Hot-wallet float limited** — keep only what's needed for caps on the
      execution wallet; sweep excess.
- [ ] **Security review gate** — an external/second-party review of the execution
      path, key handling, and guards **before** live. Non-negotiable for real
      funds.
- [ ] **Dependency & RPC trust** — a paid, authenticated RPC for execution (not
      the public endpoints already flagged as rate-limited); a malicious/faulty
      RPC can grief execution.

## 6. Prerequisites — Legal / business (Phase 3 — **user-owned, not code**)

These are **not** things Claude can decide or implement. Flagged for the account
holder; they gate live trading regardless of code readiness:

- [ ] **Legal entity** — who operates the service / holds the wallet.
- [ ] **Custody model & regulatory posture** — trading on users' behalf and/or
      with pooled funds has money-transmission / custody / securities
      implications that vary by jurisdiction. Get advice before touching user
      funds.
- [ ] **ToS, privacy policy, risk disclaimers** — especially "this can lose
      money," no-guarantee, and non-advice language.
- [ ] **Pricing / fee model** (ties into the Pro-tier billing workstream).
- [ ] **Insurance / incident policy** for loss of funds.

## 7. The flip checklist — ALL must be true before `BYREAL_DRY_RUN=false`

1. [ ] §3 technical items done, Arbitrum-only for v1.
2. [ ] Absolute per-trade + per-day USD caps live and tested.
3. [ ] Kill switch tested (halts execution with no redeploy).
4. [ ] Key custody moved off plaintext env; hot-wallet float capped.
5. [ ] Security review of the execution path completed and signed off.
6. [ ] Shadow/paper run on live rails clean for an agreed period.
7. [ ] Execution alerting + monitoring live.
8. [ ] Legal/business gate (§6) cleared by the account holder.
9. [ ] Paid execution RPC configured.

Only when **all nine** are true: flip on **Arbitrum only**, at the **smallest
possible cap**, watched live.

## 8. Recommended sequence

1. Keep Execute dry-run. Do the **safe Phase 2 work first** (Discord/LINE,
   billing/Pro tier) to build real product + revenue while Execute's gate is
   assembled.
2. Land §3 code prerequisites (caps, kill switch, idempotency, real slippage)
   behind the dry-run flag — safe to build now, changes nothing live.
3. Resolve §5 custody + §6 legal in parallel (these are the long poles).
4. Shadow-run on live rails.
5. Flip Arbitrum at minimal size once §7 is fully green.
