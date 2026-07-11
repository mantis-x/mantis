# Mantis — Project State

_Snapshot for session handoff. Written to avoid re-deriving context from a long chat history._

## Current Architecture

Multi-chain signal-detection + execution pipeline, monorepo under `packages/`:

- **`packages/ingestion`** — `ChainCollector` polls EVM chains via `web3.py`, `EventNormaliser` (`src/decoders/event_normaliser.py`) decodes raw logs into `NormalisedEvent`s, `PriceOracle` prices them. Chain configs live in `src/chains.py` (`ALL_CHAINS` registry: `mantle`, `arbitrum`, `hashkey`). Enabled chains selected via `CHAINS` env var.
- **`packages/detection`** — `detector.py` runs z-score anomaly detection per pool, seeded with synthetic baseline history (`_build_synthetic_history()`). Per-chain thresholds via `ZSCORE_THRESHOLD` / `ZSCORE_THRESHOLD_ARBITRUM`.
- **`packages/enrichment`** — `Enricher` (`src/enricher.py`) calls the Claude API (`claude-sonnet-5` as of the latest fix) to classify anomaly candidates into `Signal` objects. Stateless, retry logic in `_call_claude()`.
- **`packages/executor`** — trade execution. `arbitrum/swap_executor.py` executes via Uniswap V3 SwapRouter02 + QuoterV2, with real slippage protection, live Chainlink ETH/USD pricing, gas-price buffering for Arbitrum's fast base-fee drift, and a position-cap guard checked against real wallet balance. `identity/erc8004_logger.py` logs execution/agent-identity to chain — **hardcoded to `MANTLE_RPC_URL` regardless of origin chain** (known, accepted scoping — see Known Bugs).
- **`packages/delivery`** — fans out signals to Telegram/Discord/LINE bots. `audit/on_chain_logger.py` also **hardcoded to `MANTLE_RPC_URL`** for on-chain audit logging (same scoping as executor's logger).
- **`packages/shared`** — SQLAlchemy models + Alembic migrations (Postgres). `AgentRegistry`, subscription management, 5 tables total.
- **`contracts/`** — Hardhat project. `SignalAuditLog.sol` + `AgentIdentity.sol` deployed on Mantle mainnet. `hardhat.config.js` now also has a `hashkey` mainnet network entry (chain_id 177) — **not yet deployed there**.
- **Deploy**: Docker multi-stage build, `supervisord` runs 6 workers (ingestion, detection, enrichment, delivery, tracking, executor). `docker-entrypoint.sh` waits for Postgres then runs `alembic upgrade head` before `exec supervisord` — auto-migration wiring, verified end-to-end against a fresh Postgres container.
- **Hosting**: Railway (multi-service, Postgres + Redis + workers). This is the actual ongoing cost driver — Claude API and gas costs are both near-zero by comparison (see below).

### Supported chains
| Chain | chain_id | Signal source | Status |
|---|---|---|---|
| Mantle | 5000 | DEX swaps (Agni etc.) | Live, contracts deployed |
| Arbitrum | 42161 | Uniswap V3 swaps + GMX V1 perps | Live, execution wired |
| HashKey Chain | 177 | ERC-20 Transfer flow monitoring (no DEX with real volume found) | **Live — contracts deployed on testnet (133) and mainnet (177)** |

## Completed Work (this session and recent history)

1. **Arbitrum execution build-out**: Uniswap V3 swap executor, GMX V1 perp decoder, live Chainlink pricing, real slippage protection, gas-price buffering, signal-queue race fix, position-cap guard using real wallet balance, `execution_price` computation on `ExecutionResult`.
2. **Postgres persistence + track-record instrumentation** (`3d59adf`).
3. **Auto-migration wiring**: `docker-entrypoint.sh` now runs Alembic before workers start; verified live against a fresh Postgres container (`3fea9dd`).
4. **Safety/correctness pass**: added missing `[program:executor]` block to `supervisord.conf` (the trade-execution worker was never actually started in the deployed path); wired real `execution_price` (`337ba0b`).
5. **HashKey Chain (177) full expansion** (`d55ec59`):
   - `chains.py`: `HASHKEY` config, 3-token transfer-flow pool registry (USDT/WETH/WHSK), no Chainlink feeds (none found on-chain).
   - `event_normaliser.py`: new `_decode_token_transfer()` for ERC-20 `Transfer` events.
   - **Critical bug found and fixed in the same commit**: every decoder in `event_normaliser.py` called `log_["data"].startswith("0x")`, which throws `TypeError` on a real `HexBytes` object (only works on plain `str`). This exception was silently swallowed by `normalise()`'s outer `except Exception` at debug level — **no decoder had ever successfully parsed a single real on-chain event before this fix**; all prior "live signal" evidence must have come from synthetic data or manual scripts. Fixed via shared `_log_data()` helper. Verified: 173/177 real logs now decode correctly. Added 5 regression tests using genuine `HexBytes`-typed fixtures (`TestHexBytesRealWorldShape`) specifically because the old test suite only ever used plain-string fixtures, which is why it never caught this.
   - `detector.py`: synthetic baselines added for the 3 HashKey pools.
   - `hardhat.config.js`: `hashkey` mainnet network added (testnet RPC from the plan doc was dead — DNS didn't resolve — so no testnet entry added).
   - `.env.example`, `docs/analytics.html` updated for 3-chain state. `docs/index.html` deliberately left alone until contracts are actually live on HashKey.
6. **Retired-model bug fix** (`0639c7c`, just committed): `enricher.py` and `worker.py` were hardcoded to `claude-sonnet-4-20250514`, which **retired 2026-06-15**. Every enrichment call since then was silently failing (caught by generic `except anthropic.APIError`, logged at error level, returned `None` — no loud alarm). **No signal had been enriched for ~3 weeks.** Fixed by switching to `claude-sonnet-5`. Tests pass (11/11 in `packages/enrichment`).
7. **HashKey Chain testnet deployment** (2026-07-11): the doc-provided testnet RPC (`hashkeychain-testnet.alt.technology`) is a retired/replaced testnet — found the current one (`testnet.hsk.xyz`, chain_id **133**, live-verified via `eth_chainId`). Added `hashkeyTestnet` network to `hardhat.config.js` and `HASHKEY_TESTNET_RPC_URL` to `.env.example`. Deployer wallet (`0xbd1C2da129DA73308Fa9F2E19F633A2A3972Dc80`) turned out to already hold 0.1 testnet HSK (free faucet at `hsk.xyz/faucet`, 24h rate limit, no funding blocker for testnet). Deployed both contracts to HashKey testnet and verified bytecode on-chain via `eth_getCode`:
   - `SignalAuditLog`: `0xd745Fc0c28B8755b6280232a179e21C50B1D3adf` (smoke test passed — logSignal + verify)
   - `AgentIdentity`: `0x06036B53A1f8d2Cf691a6f324C0672eB6D865667`
   - Receipts saved under `contracts/deployments/hashkeyTestnet/`. Gas cost negligible (~0.002 HSK total spent).
   - Note: these addresses are byte-identical to the ones already in `.env` for `AUDIT_CONTRACT_ADDRESS` / `AGENT_IDENTITY_CONTRACT_ADDRESS` (Mantle mainnet) — expected, not a bug: CREATE address only depends on `(deployer, nonce)`, and the deployer used the same first-two-nonces sequence on both chains. Root `.env` was **not** modified — those var names are already claimed by the Mantle deployment; HashKey testnet/mainnet addresses need their own var names once the audit-logger chain-scoping bug (see Known Bugs) is actually fixed.
   - Mainnet deployer balance checked at the time: still **0 HSK** — funding was still the only blocker for the mainnet deploy.
8. **HashKey Chain mainnet deployment** (2026-07-11): wallet funded with 133.18 HSK. Deployed both contracts to HashKey mainnet (chain_id 177), verified bytecode on-chain via `eth_getCode`:
   - `SignalAuditLog`: `0xd745Fc0c28B8755b6280232a179e21C50B1D3adf` (smoke test passed)
   - `AgentIdentity`: `0x06036B53A1f8d2Cf691a6f324C0672eB6D865667`
   - Same addresses as testnet — expected (deterministic CREATE, same nonce sequence). Also byte-identical to the pre-existing Mantle mainnet addresses in `.env` — pure coincidence of address derivation, does **not** mean these are the same contract instance; each is a separate deployment on its own chain.
   - Gas cost negligible (~0.016 HSK spent, ~133.16 HSK remaining).
   - User confirmed `CHAINS=hashkey` is set on Railway. **Flagged and fixed same session** — see Completed Work #9.
9. **Audit-logger chain-scoping fix** (2026-07-11): `packages/delivery/src/audit/on_chain_logger.py` and `packages/executor/src/identity/erc8004_logger.py` no longer hardcode `MANTLE_RPC_URL`. Both now take a `chain` param and resolve RPC/contract/explorer per chain via `_CHAIN_RPC_ENV` / `_CHAIN_CONTRACT_ENV` / `_CHAIN_EXPLORER` maps (mantle/arbitrum/hashkey). Contract-address env vars fall back to the existing shared `AUDIT_CONTRACT_ADDRESS` / `AGENT_IDENTITY_CONTRACT_ADDRESS` (correct today since CREATE addresses happen to be identical across chains — see #7/#8) but can be overridden per chain via `ARBITRUM_*`/`HASHKEY_*` env vars (added to `.env.example`, unset by default) if a future deploy ever diverges.
   - `packages/delivery/src/worker.py`: replaced the single `audit_logger` with a `get_audit_logger(chain)` lazy cache, keyed by `signal["chain"]`, so each origin chain gets its own logger and a missing/undeployed chain (e.g. Arbitrum, no `SignalAuditLog` there yet) degrades gracefully per-chain instead of the whole dispatcher failing.
   - `packages/executor/src/executor.py`: replaced the single `self.identity` with `self._identity_logger(chain)`, keyed by `request.chain`, called at the `log_decision` site.
   - Verified live against real RPCs (not mocked): constructed `OnChainLogger`/`ERC8004Logger` for all three chains and confirmed each connects and reports the correct on-chain `chain_id` (mantle=5000, hashkey=177, arbitrum=42161) with the right explorer URL.
   - Existing test suites (`packages/executor`, `packages/delivery`) still pass — 66/66 and 16/16 respectively (excluding pre-existing DB-dependent test errors unrelated to this change, which need a live Postgres).
   - Known residual gap, not fixed (out of scope for this pass): if a HashKey-originated signal ever matched an agent's execution rules, `executor.py`'s `_do_swap` has no `chain == "hashkey"` branch and would fall through to the Mantle/Byreal swap path — harmless today since HashKey signals are directional flow-monitoring only and don't drive trade execution, but worth a guard if that ever changes.

## Pending Tasks

1. **Done**: HashKey deployer wallet funded (133.18 HSK) and both contracts deployed to mainnet — see Completed Work #8. Full 3-chain contract parity achieved (Mantle, Arbitrum-pending-its-own-deploy, HashKey all have `SignalAuditLog`/`AgentIdentity` live — note Arbitrum's contracts still not deployed per the chain table above, only execution is wired there).
2. **Done**: audit-logger chain-scoping bug fixed — see Completed Work #9. Deploy the updated `packages/executor` and `packages/delivery` code to Railway (this was a code-only fix, no new env vars required — the existing `AUDIT_CONTRACT_ADDRESS`/`AGENT_IDENTITY_CONTRACT_ADDRESS` work as the fallback for every chain).
3. Verify in production that the `claude-sonnet-5` enrichment fix is actually live (redeploy Railway service, watch logs for successful `✅ Signal enriched` lines instead of `Claude API error`).
4. Set up Anthropic Console billing for `ANTHROPIC_API_KEY` if not already done — API billing is fully separate from any Claude Pro/Max subscription (prepaid credits or pay-as-you-go card, metered per token). Estimated cost is trivial (~$2–5.50/month at current signal volume, Sonnet 5 intro pricing through 2026-08-31).
5. Watch Railway credit balance — this is the actual ongoing cost driver, not API/gas. Trial was down to "2 days or $1.39 left" as of 2026-07-11; recent burn rate (~$0.70/day, up from ~$0.17/day historically) projects to ~$20–25/month once on a paid plan — Hobby ($5/mo) has no usage cap but that's a minimum fee, not a ceiling, so expect real usage-based overage on top of it.

## Known Bugs / Risks

- ~~On-chain audit/decision logging is Mantle-only~~ **Fixed 2026-07-11** — see Completed Work #9. Both loggers now route by origin chain.
- **Silent failure pattern in enrichment**: `_call_claude()`'s `except anthropic.APIError` / generic `except Exception` handlers log and return `None` with no alerting — this is exactly how the retired-model bug went unnoticed for 3 weeks. No monitoring/alerting currently wired for repeated enrichment failures. Worth adding a metric or alert if failures spike (e.g. Railway log-based alert, or a simple failure-rate counter surfaced somewhere visible).
- **Arbitrum public RPC rate-limiting risk**: `.env.example` flags that the public `arb1.arbitrum.io/rpc` endpoint rate-limits `get_logs` at high batch sizes. No paid RPC key currently configured. Not yet an active problem, but a production-uptime risk to watch.
- **HashKey has no verified Chainlink price feed** — `HASHKEY.price_feeds == {}`, prices are static (`token_prices` dict), not live-updated. Acceptable for now since HashKey signals are directional flow monitoring, not precise USD-value-dependent trading decisions like Arbitrum.

## Next Priorities

1. Fund HashKey deployer wallet and deploy the two contracts (only step left for full 3-chain parity).
2. Confirm the Sonnet-5 enrichment fix is live in production and signals are flowing again (this was silently broken for weeks — verify, don't assume).
3. Decide whether to fix the Mantle-only audit-logging scoping now or accept it as a known limitation for the near term.
4. Keep an eye on Railway balance/top-ups — it's the one real recurring cost with actual risk of causing an outage if it runs out.
