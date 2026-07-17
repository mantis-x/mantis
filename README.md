# Mantis

> See the move. Make the move.

Signal-to-execution AI running on **Mantle**, **Arbitrum**, and **HashKey Chain**.

---

## What Mantis is

**Mantis Scout** is the intelligence layer. Monitors Mantle DeFi protocols
(Agni Finance, Merchant Moe, Fluxion), Arbitrum protocols (Uniswap V3,
Trader Joe, GMX V1 perps), and HashKey Chain ERC-20 transfer flows around
the clock. Detects smart money wallet clusters using z-score anomaly
detection, enriches each signal through Claude Sonnet 5, and delivers
plain-English alerts with confidence scores to Telegram, Discord, and LINE.
Every signal is hashed and recorded immutably on-chain via
`SignalAuditLog.sol` — fully auditable, forever.

**Mantis Execute** is the execution layer. An intent-based agentic wallet
powered by the Byreal Skills CLI. Define your intent once — "follow smart
money into mETH pools when confidence exceeds 75" — and the agent evaluates
safety guards, sizes the position, and executes autonomously. Every decision,
including aborts, is logged to the agent's ERC-8004 on-chain identity via
`AgentIdentity.sol`.

Both products share one backend pipeline and one Railway deployment.
Scout is the human interface. Execute is the autonomous layer.

---

## Architecture

```
                ┌─ ChainCollector(mantle)   ─┐
                │                            │
ingestion ──────┼─ ChainCollector(arbitrum) ─┼──► Redis mantis:raw_events
 (1 process)    │                            │         │
                └─ ChainCollector(hashkey)  ─┘         ▼
                                              detection — baselines keyed (chain, pool)
                                                        │
                                                        ▼
                                 enrichment — Claude Sonnet 5 (chain-aware)
                                                        │
                              ┌─────────────────────────┤
                              ▼                         ▼
                         delivery                   executor
                 chain badge + per-chain      Mantle + Arbitrum: live trading
                 audit log (all 3 chains)     HashKey: alert-only (flow monitoring)
                  Telegram / Discord / LINE
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
│   └── executor/    # Intent engine + Byreal execution + ERC-8004
├── contracts/       # Solidity: SignalAuditLog.sol + AgentIdentity.sol
├── scripts/         # check_rpc_health.py · replay_block_range.py
└── docs/            # Architecture, API reference, expansion plan
```

---

## Quick start

```bash
cp .env.example .env        # fill in API keys
docker-compose up -d        # start Postgres + Redis
make migrate                # run DB migrations
make ingest                 # start ingestion worker (CHAINS=mantle,arbitrum,hashkey)
make detect                 # start detection + enrichment
make deliver                # start Scout bots (Telegram, Discord, LINE)
make execute                # start Execute agent
```

---

## Contracts

| Contract | Mantle mainnet | Arbitrum mainnet | HashKey mainnet |
|---|---|---|---|
| `SignalAuditLog.sol` | `0xd745Fc0c28B8755b6280232a179e21C50B1D3adf` | `0x06036B53A1f8d2Cf691a6f324C0672eB6D865667` | `0xd745Fc0c28B8755b6280232a179e21C50B1D3adf` |
| `AgentIdentity.sol` | `0x06036B53A1f8d2Cf691a6f324C0672eB6D865667` | `0x41D656CC959B6CA547A400F9031321FC405D70ef` | `0x06036B53A1f8d2Cf691a6f324C0672eB6D865667` |

> **All three chains now have real mainnet contracts.** Mantle and Arbitrum were deployed 2026-07-16, closing out the last chain-deployment gap. Note Arbitrum's addresses do **not** match the other chains' shared pattern — the deployer's Arbitrum nonce was already at 3 from unrelated prior activity, so `SignalAuditLog` landed on the address every other chain uses for `AgentIdentity`. Set via `ARBITRUM_AUDIT_CONTRACT_ADDRESS` / `ARBITRUM_AGENT_IDENTITY_CONTRACT_ADDRESS` overrides (already configured on Railway) — do not assume the shared default addresses apply to Arbitrum. Testnets (Mantle Sepolia, Arbitrum Sepolia, HashKey testnet) also still exist from earlier development, see `contracts/deployments/`.

Deploy: `npx hardhat run scripts/deploy_audit_log.js --network arbitrum` (swap `--network` for `mantle` / `hashkey`, then `deploy_agent_identity.js` the same way)

---

## Environment variables

| Variable | Used by |
|---|---|
| `CHAINS` | ingestion — comma-separated chain names (`mantle,arbitrum,hashkey`) |
| `MANTLE_RPC_URL` | ingestion, contracts |
| `ARBITRUM_RPC_URL` | ingestion — defaults to public `arb1.arbitrum.io/rpc` (rate-limit risk, no paid key configured) |
| `HASHKEY_RPC_URL` | ingestion, contracts — HashKey mainnet (chain_id 177) |
| `HASHKEY_TESTNET_RPC_URL` | contracts — HashKey testnet (chain_id 133) |
| `ANTHROPIC_API_KEY` | enrichment — Claude Sonnet 5 |
| `TELEGRAM_BOT_TOKEN` | delivery — Mantis Scout Telegram bot |
| `DISCORD_BOT_TOKEN` | delivery — Discord bot (optional) |
| `LINE_CHANNEL_ACCESS_TOKEN` / `LINE_CHANNEL_SECRET` | delivery — LINE bot (optional) |
| `BYREAL_PRIVATE_KEY` | executor — Execute agent wallet |
| `AUDIT_CONTRACT_ADDRESS` | delivery, executor — SignalAuditLog.sol / AgentIdentity.sol shared default; override per chain via `ARBITRUM_*` / `HASHKEY_*` prefixed vars if a deploy ever diverges |
| `AGENT_IDENTITY_CONTRACT_ADDRESS` | executor — AgentIdentity.sol |
| `ZSCORE_THRESHOLD` | detection — global anomaly threshold (default `2.5`) |
| `ZSCORE_THRESHOLD_ARBITRUM` | detection — per-chain override (default `3.5`) |
| `NANSEN_API_KEY` | ingestion — wallet intelligence |
| `ELFA_API_KEY` | enrichment — protocol sentiment |

---

## Chain support

| Chain | Status | Protocols | Execution |
|---|---|---|---|
| Mantle | Live | Agni Finance (verified real pools; Merchant Moe, Fluxion unverified) | Scout + Execute (audit/identity contracts live on mainnet; Execute itself still `BYREAL_DRY_RUN=true`, simulated) |
| Arbitrum | Live | Uniswap V3, Trader Joe, GMX V1 perps | Scout + Execute (audit/identity contracts live on mainnet, requires `ARBITRUM_*` address overrides — see Contracts) |
| HashKey Chain | Live | ERC-20 transfer flow monitoring (no DEX with real volume found) | Scout only — flow-monitoring signals don't drive trade execution |

Adding a new chain: add a `ChainConfig` entry in `packages/ingestion/src/chains.py`, set `CHAINS=mantle,arbitrum,hashkey,<new>` — no other code changes needed.

---

## Scripts

```bash
# Check RPC health for all enabled chains
python scripts/check_rpc_health.py --chain mantle,arbitrum,hashkey

# Replay a historical block range and print decoded events
python scripts/replay_block_range.py --chain arbitrum --from 479900000 --to 479900500 --min-usd 100000
```

---

## Stack

Python 3.11 · Solidity 0.8.20 · web3.py · Claude Sonnet 5 · Byreal Skills CLI ·
Nansen API · Elfa AI · Telegram Bot API · Discord API · LINE Messaging API ·
Postgres · Redis · Hardhat · Docker · Mantle L2 · Arbitrum L2 · HashKey Chain
