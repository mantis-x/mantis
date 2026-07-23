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
- [x] **Hard per-trade and per-day notional caps** — **Done 2026-07-23.**
      `guard_runner.py`'s `_absolute_trade_cap`/`_absolute_daily_cap` (env
      `MAX_TRADE_USD`=$50, `MAX_DAILY_TRADE_USD`=$200 default), independent of
      the % guard. Daily spend tracked in Redis by `packages/executor/src/safety.py`,
      read/written by `worker.py` each loop.
- [x] **Idempotency / double-execution guard** — **Done 2026-07-23.**
      `safety.py`'s `is_duplicate_signal()` — an atomic `SET NX` on a signal
      fingerprint (chain/protocol/pool/type/confidence/z-score/volume/detected_at,
      deliberately excluding `id` which is often `None` at this stage) — claims a
      signal before executing so a replay/duplicate push can't double-submit.
- [x] **Kill switch** — **Done 2026-07-23.** `mantis:execution:kill_switch` Redis
      key, checked every loop iteration before popping anything off the queue —
      `python scripts/execution_kill_switch.py on|off|status`. Signals stay
      queued untouched while active (halt, not drop).
- [x] **Slippage guard clarified — it was never actually a placeholder** —
      investigated 2026-07-23: `arbitrum/swap_executor.py`'s `_get_quote()`
      already calls Uniswap V3's QuoterV2 on-chain immediately before every
      swap and sets `amountOutMinimum` from that live quote — real
      quote-vs-execution slippage protection has existed all along. The
      `guard_runner.py` check is a pre-flight sanity bound on the *requested*
      tolerance, not a substitute for that — comment updated to stop
      describing it as an unfinished "hackathon" pass-through.
- [ ] **Mantle only:** package `byreal-cli` into the Docker image (or drop Mantle
      execution from v1 and go Arbitrum-only).

## 4. Prerequisites — Safety & operational

- [ ] **Start in shadow/paper on live rails** — run the real executor against
      real quotes with $0 or a $1 cap first, comparing intended vs simulated
      fills for N days before any nonzero size. **One-time manual pipeline
      validation done 2026-07-23** (not the full agreed-period run this item
      still requires): pushed one hand-crafted signal onto production
      `mantis:signals:exec`, confirmed the real executor consumed it, matched
      it via `rule_engine`, and the `guard_runner` absolute-trade-cap check
      correctly aborted it — `BYREAL_DRY_RUN=true` throughout, nothing reached
      chain. This run caught a real misconfiguration, fixed same day: the only
      registered agent's rule requested a $100 trade while `MAX_TRADE_USD`
      defaults to $50, meaning that agent could never have executed anything
      once live. Fixed by lowering the agent's `amount_usd` to $50 (both the
      `agent_registry.py` seed default and the already-seeded production DB
      row, via a direct `UPDATE agents ... rules->amount_usd`).
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

1. [ ] §3 technical items done, Arbitrum-only for v1 — **4/6 done 2026-07-23**
       (caps, idempotency, kill switch, slippage clarified). Still open: token
       approval/allowance handling, and Mantle's `byreal-cli` packaging (moot
       if going Arbitrum-only for v1).
2. [x] Absolute per-trade + per-day USD caps live and tested. **Done 2026-07-23.**
3. [x] Kill switch tested (halts execution with no redeploy). **Done 2026-07-23**
       — verified functionally against a real Redis instance.
4. [ ] Key custody moved off plaintext env; hot-wallet float capped.
5. [ ] Security review of the execution path completed and signed off.
6. [ ] Shadow/paper run on live rails clean for an agreed period. One-time
       manual pipeline validation done 2026-07-23 (see §4) — this item is
       about a sustained clean period, not satisfied by a single pass.
7. [ ] Execution alerting + monitoring live.
8. [ ] Legal/business gate (§6) cleared by the account holder.
9. [ ] Paid execution RPC configured.

Only when **all nine** are true: flip on **Arbitrum only**, at the **smallest
possible cap**, watched live.

## 8. Recommended sequence

1. Keep Execute dry-run. Do the **safe Phase 2 work first** (billing/Pro
   tier) to build real revenue while Execute's gate is assembled. Discord/LINE
   reprioritized out of active Phase 2 as of 2026-07-23 — future scale-up
   roadmap, not current-phase work (see PROJECT_STATE.md Known Bugs).
2. Land §3 code prerequisites (caps, kill switch, idempotency, real slippage)
   behind the dry-run flag — safe to build now, changes nothing live. **4/6 done 2026-07-23.**
3. Resolve §5 custody + §6 legal in parallel (these are the long poles).
4. Shadow-run on live rails.
5. Flip Arbitrum at minimal size once §7 is fully green.
