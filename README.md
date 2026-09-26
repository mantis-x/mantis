# Mantis

> See the move. Make the move.

Signal-to-execution AI running on **Mantle**, **Arbitrum**, **HashKey Chain**, and **Ethereum**.

---

## What Mantis is

**ETHGlobal submission summary:** Mantis is an AI agent that detects on-chain
anomalies, requests verified human consent for consequential trades, executes
approved Arbitrum swaps through Uniswap V3, and records every decision on-chain.

**Mantis Scout** is the intelligence layer. Monitors Mantle DeFi protocols
(Agni Finance, Merchant Moe), Arbitrum protocols (Uniswap V3,
Trader Joe, GMX V1 perps), HashKey Chain ERC-20 transfer flows, and Ethereum
mainnet blue-chip Uniswap V3 pools (WETH/USDC, WETH/USDT, WBTC/WETH) around
the clock. Detects smart money wallet clusters using z-score anomaly
detection, enriches each signal through Claude Sonnet 5, and delivers
plain-English alerts with confidence scores to Telegram (Discord/LINE ready
but not enabled).
Every signal is hashed and recorded immutably on-chain via
`SignalAuditLog.sol` — fully auditable, forever.

**Mantis Execute** is the execution layer. An intent-based agentic wallet
powered by the Byreal Skills CLI. Define your intent once — "follow smart
money into mETH pools when confidence exceeds 75" — and the agent evaluates
safety guards, sizes the position, and executes autonomously. Every decision,
including aborts, is logged to the agent's ERC-8004 on-chain identity via
`AgentIdentity.sol`.

For high-value actions, Execute adds a World ID for Agents human-consent gate:
the client submits a proof, the backend validates it with World, and only then
does the selected venue receive the swap. Missing, rejected, expired, or
unverifiable proofs fail closed and are logged as aborted decisions. Set
`WORLD_ID_APPROVAL_THRESHOLD_USD=0` to require approval for every action.

### ETHGlobal integration references

- Uniswap feedback: [`FEEDBACK.md`](FEEDBACK.md)
- Uniswap Developer Feedback Form: <https://developers.uniswap.org/hackathon-feedback>
- World OIDC client and signed artifact issuance:
  [`packages/api/src/world_oidc.py`](packages/api/src/world_oidc.py)
- World approval gate and exact intent binding:
  [`packages/executor/src/approval/world_id.py`](packages/executor/src/approval/world_id.py)
- Arbitrum Uniswap V3 SwapRouter02 and QuoterV2 integration:
  [`packages/executor/src/arbitrum/swap_executor.py`](packages/executor/src/arbitrum/swap_executor.py)

**Mantis API** is the programmatic layer ($299/mo institutional tier). The same
enriched, back-tested signals exposed as an authenticated REST feed
(`GET /v1/signals`, `/v1/signals/{id}` with PnL outcomes, `/v1/stats`) plus
HMAC-signed **webhooks** for real-time push. Headless API customers
(`api_customers` / `api_keys`, separate from chat subscribers), pay-for-a-period
crypto billing in USDC on Arbitrum. Gated behind `API_TIER_ENABLED` /
`API_TIER_PAYMENTS_ENABLED` (see the API tier section below).

All three surfaces share one backend pipeline and one Railway deployment.
Scout is the human interface, Execute is the autonomous layer, and the API is
the programmatic/data layer.

---

## Architecture

```
                ┌─ ChainCollector(mantle)   ─┐
                │                            │
                ├─ ChainCollector(arbitrum) ─┤
ingestion ──────┤                            ├──► Redis mantis:raw_events
 (1 process)    ├─ ChainCollector(hashkey)  ─┤         │
                │                            │         ▼
                └─ ChainCollector(ethereum) ─┘  detection — baselines keyed (chain, pool)
                                                        │
                                                        ▼
                                 enrichment — Claude Sonnet 5 (chain-aware)
                                                        │
                        ┌───────────────────────────────┼───────────────────────┐
                        ▼                               ▼                         ▼
                   delivery                         executor                  tracking
          chain badge + per-chain            Mantle + Arbitrum:       persists every signal to
          audit log (4 chains,               live trading             Postgres, schedules PnL
          all on mainnet)                    HashKey + Ethereum:      outcomes, and fans out
          Telegram / Discord / LINE          alert-only               to mantis:signals:api
                                                                              │
                                                                              ▼
                                              api      — authed REST feed (/v1/signals, /stats)
                                              webhooks — HMAC-signed real-time push delivery
```

---

## Monorepo structure

```
mantis/
├── packages/
│   ├── ingestion/   # Multi-chain RPC poller + decoders (Agni, UniV3, Trader Joe, ERC-20 flows…)
│   ├── detection/   # Z-score anomaly detection + wallet clustering
│   ├── enrichment/  # LLM signal enrichment via Claude Sonnet 5
│   ├── delivery/    # Telegram + Discord + LINE bots + per-chain on-chain audit logger
│   ├── executor/    # Intent engine + Byreal execution + ERC-8004
│   ├── api/         # API tier: FastAPI read feed + webhook dispatcher (Phases A/B/C)
│   └── shared/      # SQLAlchemy models + Alembic migrations + tracking/billing watchers
├── contracts/       # Solidity: SignalAuditLog.sol + AgentIdentity.sol
├── scripts/         # check_rpc_health.py · replay_block_range.py · verify_pool.py · mint_api_key.py …
└── docs/            # Architecture, API reference, expansion plan, execute/api readiness
```

---

## Quick start

```bash
cp .env.example .env        # fill in API keys
docker-compose up -d        # start Postgres + Redis
make migrate                # run DB migrations
make ingest                 # start ingestion worker (CHAINS=mantle,arbitrum,hashkey,ethereum)
make detect                 # start detection + enrichment
make deliver                # start Scout bots (Telegram, Discord, LINE)
make execute                # start Execute agent
```

---

## Contracts

| Contract | Mantle mainnet | Arbitrum mainnet | HashKey mainnet | Ethereum mainnet |
|---|---|---|---|---|
| `SignalAuditLog.sol` | `0xd745Fc0c28B8755b6280232a179e21C50B1D3adf` | `0x06036B53A1f8d2Cf691a6f324C0672eB6D865667` | `0xd745Fc0c28B8755b6280232a179e21C50B1D3adf` | `0x3efa7b94cbB56c9046ffB10a999dB5ba8220DD85` |
| `AgentIdentity.sol` | `0x06036B53A1f8d2Cf691a6f324C0672eB6D865667` | `0x41D656CC959B6CA547A400F9031321FC405D70ef` | `0x06036B53A1f8d2Cf691a6f324C0672eB6D865667` | `0x311E456e0A0786b36B6152fbc83cb4078bc0AE13` |

> **All four chains now have real mainnet contracts.** Ethereum was deployed 2026-07-18 (tx `0x2a78d136ece0…56d46` / `0xd3f09127a0cc…de7fe`), going straight to mainnet rather than staging on Sepolia testnet first, since no Sepolia ETH was available for the deployer wallet. `ETHEREUM_RPC_URL` drives both ingestion and on-chain audit logging, same as every other chain (no split env vars). Live gas-checked before this decision: Ethereum mainnet gas was ~0.06–0.07 gwei at the time, comparable to Mantle/Arbitrum's cost, not the "expensive L1" assumption that originally justified deferring this. Total deploy cost: ~0.0002 ETH (~$0.35) for both contracts + smoke test.
>
> Note **Arbitrum and Ethereum both** have addresses that do **not** match the other chains' shared pattern — both deployer wallets had unrelated prior nonce activity on those chains before this project's first deploy there, so the CREATE addresses landed differently. Set via `ARBITRUM_AUDIT_CONTRACT_ADDRESS`/`ARBITRUM_AGENT_IDENTITY_CONTRACT_ADDRESS` and `ETHEREUM_AUDIT_CONTRACT_ADDRESS`/`ETHEREUM_AGENT_IDENTITY_CONTRACT_ADDRESS` overrides — never assume the shared default addresses apply to either chain. Testnets (Mantle Sepolia, Arbitrum Sepolia, HashKey testnet) also still exist from earlier development, see `contracts/deployments/`.
>
> Historical deployment note: an Ethereum public-RPC response-formatting issue once caused ethers.js to report `invalid value for value.to` after both transactions had already confirmed on-chain (`eth_getTransactionReceipt`, `status: 0x1`). The deployment scripts now poll receipts through raw JSON-RPC and derive contract-creation addresses from the deployer nonce. Existing Ethereum deployment records remain in `PROJECT_STATE.md` and `contracts/deployments/ethereum/`.

Deploy: `npx hardhat run scripts/deploy_audit_log.js --network arbitrum` (swap `--network` for `mantle` / `hashkey` / `ethereumSepolia`, then `deploy_agent_identity.js` the same way)

---

## Environment variables

| Variable | Used by |
|---|---|
| `CHAINS` | ingestion — comma-separated chain names (`mantle,arbitrum,hashkey,ethereum`) |
| `MANTLE_RPC_URL` | ingestion, contracts |
| `ARBITRUM_RPC_URL` | ingestion — defaults to public `arb1.arbitrum.io/rpc` (rate-limit risk, no paid key configured) |
| `HASHKEY_RPC_URL` | ingestion, contracts — HashKey mainnet (chain_id 177) |
| `HASHKEY_TESTNET_RPC_URL` | contracts — HashKey testnet (chain_id 133) |
| `ETHEREUM_RPC_URL` | ingestion **and** on-chain audit — Ethereum mainnet (chain_id 1); Ethereum's audit contracts are on **mainnet** (not Sepolia — see Contracts), driven by this same var. Public RPCs here are stricter on `eth_getLogs` range than Mantle/Arbitrum's |
| `ETHEREUM_SEPOLIA_RPC_URL` | contracts only — optional Ethereum Sepolia (chain_id 11155111) network for Hardhat; vestigial (mainnet-direct deploy was used, no Sepolia staging) |
| `ANTHROPIC_API_KEY` | enrichment — Claude Sonnet 5 |
| `TELEGRAM_BOT_TOKEN` | delivery — Mantis Scout Telegram bot |
| `DISCORD_BOT_TOKEN` | delivery — Discord bot (optional) |
| `LINE_CHANNEL_ACCESS_TOKEN` / `LINE_CHANNEL_SECRET` | delivery — LINE bot (optional) |
| `BYREAL_PRIVATE_KEY` | executor — Execute agent wallet |
| `WORLD_ID_APPROVAL_THRESHOLD_USD` | executor — USD threshold for mandatory human approval (default `100`) |
| `WORLD_ID_CLIENT_ID` / `WORLD_ID_CLIENT_SECRET` / `WORLD_ID_REDIRECT_URI` | api — World ID Agents OIDC client credentials and exact callback URI |
| `WORLD_ID_ISSUER` / `WORLD_ID_STATE_SECRET` / `WORLD_ID_ARTIFACT_TTL_SECONDS` | api + executor — OIDC issuer, shared artifact-signing secret, and approval lifetime |
| `AUDIT_CONTRACT_ADDRESS` | delivery, executor — SignalAuditLog.sol / AgentIdentity.sol shared default; override per chain via `ARBITRUM_*` / `HASHKEY_*` / `ETHEREUM_*` prefixed vars if a deploy ever diverges (Ethereum's will always diverge — it's on a different network entirely) |
| `AGENT_IDENTITY_CONTRACT_ADDRESS` | executor — AgentIdentity.sol |
| `ZSCORE_THRESHOLD` | detection — global anomaly threshold (default `2.5`) |
| `ZSCORE_THRESHOLD_<CHAIN>` | detection — optional per-chain override; **not set in production** (falls back to the global `2.5`) — raising it also throttles the multi-wallet path, the only current source of delivered Arbitrum signals |
| `MIN_CANDIDATE_USD` / `SOLO_MIN_USD_<CHAIN>` / `MULTIWALLET_MIN_USD[_<CHAIN>]` | detection — pre-enrichment, solo, and multi-wallet USD floors for noise/cost control (e.g. `SOLO_MIN_USD_ETHEREUM=500000`, `MULTIWALLET_MIN_USD_ETHEREUM=1000000`) |
| `API_TIER_ENABLED` | api — gates all authed API routes (404 when false); `/v1/health` stays up regardless |
| `API_TIER_PAYMENTS_ENABLED` | shared/tracking — enables the API-tier USDC payment watcher (independent of `API_TIER_ENABLED`) |
| `API_TIER_RECEIVE_ADDRESS` | shared — Arbitrum wallet API payments are sent to; **must differ from `PRO_TIER_RECEIVE_ADDRESS`** (shared address cross-credits) |
| `API_TIER_PRICE_USDC` | api, shared — 1-month price (default `299`); 6mo/12mo tiers derived with discounts |
| `PRO_TIER_PAYMENTS_ENABLED` · `PRO_TIER_RECEIVE_ADDRESS` · `PRO_TIER_PRICE_USDC` | shared/tracking, delivery — Telegram Pro tier crypto billing (USDC on Arbitrum) |
| `NANSEN_API_KEY` | enrichment — optional smart-money wallet labels (fallback to Mantis's own track record when unset) |
| `ELFA_API_KEY` | enrichment — protocol sentiment |

---

## Chain support

| Chain | Status | Protocols | Execution |
|---|---|---|---|
| Mantle | Live | Agni Finance (3 pools) + Merchant Moe (1 pool), all verified real via `eth_getLogs`; Fluxion removed 2026-07-19 — old address was fake, no real replacement found yet | Scout + Execute (audit/identity contracts live on mainnet; Execute itself still `BYREAL_DRY_RUN=true`, simulated) |
| Arbitrum | Live | Uniswap V3, Trader Joe, GMX V1 perps | Scout + Execute (audit/identity contracts live on mainnet, requires `ARBITRUM_*` address overrides — see Contracts) |
| HashKey Chain | Live | ERC-20 transfer flow monitoring (no DEX with real volume found) | Scout only — flow-monitoring signals don't drive trade execution |
| Ethereum | Live (2026-07-18) | Uniswap V3 blue-chip pools — USDC/WETH, WETH/USDT, WBTC/WETH, all 0.05% tier (verified real via `eth_getLogs`, not assumed) | Scout only — no swap executor built; `executor.py._do_swap`/`_wallet_balance_usd` explicitly raise rather than silently falling through to the Mantle path. Audit/identity contracts live on mainnet, requires `ETHEREUM_*` address overrides — see Contracts |

Adding a new chain: add a `ChainConfig` entry in `packages/ingestion/src/chains.py`, set `CHAINS=mantle,arbitrum,hashkey,ethereum,<new>` — no other code changes needed for ingestion/detection. Delivery (bot chain lists, `CHAIN_META`) and executor (`_do_swap` routing) still need manual updates per chain — see Ethereum's build-out for the full list of touchpoints.

---

## API tier

Programmatic access to the same enriched signals ($299/mo institutional tier).
FastAPI app (`packages/api`, auto-docs at `/docs`) + a webhook dispatcher, both
run as their own workers. Auth is `Authorization: Bearer <key>`; keys are stored
hashed and minted with `scripts/mint_api_key.py`.

| Endpoint | Purpose |
|---|---|
| `GET /v1/health` | Liveness (unauthenticated) |
| `GET /v1/signals` | List signals — filters `chain`, `signal_type`, `min_confidence`, `since`; cursor pagination |
| `GET /v1/signals/{id}` | One signal + its 1h/4h/24h/7d PnL outcomes |
| `GET /v1/stats` | Per-chain signal counts + directional hit-rate |
| `POST/GET/DELETE /v1/webhooks` · `/{id}/test` | Manage HMAC-signed webhook subscriptions (per-customer, SSRF-guarded) |
| `GET /v1/billing` · `POST /v1/billing/wallet` | Pricing/status + register the paying wallet |

**Webhooks** POST each new signal with `X-Mantis-Signature` (HMAC-SHA256 over
`{timestamp}.{body}`) + `X-Mantis-Timestamp`; verify with the `whsec_` secret
returned once at registration. Bounded-concurrency delivery, backoff retries,
consecutive-failure auto-disable.

**Billing** is pay-for-a-period USDC on Arbitrum (1mo `299` / 6mo −5% / 12mo
−10%), matched by a separate receive address (never Pro's). Everything is gated
by `API_TIER_ENABLED` / `API_TIER_PAYMENTS_ENABLED` (default off). See
`docs/api_tier_readiness.md` and `docs/api_tier_build_plan.md`.

> **Not yet cleared before serving external customers** (billing is functional
> but these gate real go-live): security review of the public surface, SSRF
> DNS-rebind hardening, a paid RPC / SLA, and the legal/entity/ToS decision.

---

## Scripts

```bash
# Check RPC health for all enabled chains
python scripts/check_rpc_health.py --chain mantle,arbitrum,hashkey,ethereum

# Replay a historical block range and print decoded events
python scripts/replay_block_range.py --chain arbitrum --from 479900000 --to 479900500 --min-usd 100000
```

---

## Stack

Python 3.11 · Solidity 0.8.20 · web3.py · Claude Sonnet 5 · Byreal Skills CLI ·
FastAPI · uvicorn · Nansen API · Elfa AI · Telegram Bot API · Discord API · LINE Messaging API ·
Postgres · Redis · Hardhat · Docker · Mantle L2 · Arbitrum L2 · HashKey Chain · Ethereum
