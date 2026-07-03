# Mantis

> See the move. Make the move.

Signal-to-execution AI running on **Mantle** and **Arbitrum**.

---

## What Mantis is

**Mantis Scout** is the intelligence layer. Monitors Mantle DeFi protocols
(Agni Finance, Merchant Moe, Fluxion) and Arbitrum protocols (Uniswap V3,
Trader Joe) around the clock. Detects smart money wallet clusters using
z-score anomaly detection, enriches each signal through Claude Sonnet, and
delivers plain-English alerts with confidence scores to Telegram, Discord,
and LINE. Every signal is hashed and recorded immutably on-chain via
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
ingestion ──────┤                            ├──► Redis mantis:raw_events
 (1 process)    └─ ChainCollector(arbitrum) ─┘         │
                                                        ▼
                              detection — baselines keyed (chain, pool)
                                                        │
                                                        ▼
                                   enrichment — Claude Sonnet (chain-aware)
                                                        │
                              ┌─────────────────────────┤
                              ▼                         ▼
                         delivery                   executor
                  chain badge + per-chain       Mantle-only (phase 1)
                    explorer links              Arbitrum: alert-only
                  Telegram / Discord / LINE
```

---

## Monorepo structure

```
mantis/
├── packages/
│   ├── ingestion/   # Multi-chain RPC poller + decoders (Agni, UniV3, Trader Joe…)
│   ├── detection/   # Z-score anomaly detection + wallet clustering
│   ├── enrichment/  # LLM signal enrichment via Claude Sonnet
│   ├── delivery/    # Telegram + Discord + LINE bots + on-chain audit logger
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
make ingest                 # start ingestion worker (CHAINS=mantle,arbitrum)
make detect                 # start detection + enrichment
make deliver                # start Scout bots (Telegram, Discord, LINE)
make execute                # start Execute agent
```

---

## Contracts

| Contract | Mantle Sepolia | Arbitrum Sepolia |
|---|---|---|
| `SignalAuditLog.sol` | `0xd745Fc0c28B8755b6280232a179e21C50B1D3adf` | `0xd745Fc0c28B8755b6280232a179e21C50B1D3adf` |
| `AgentIdentity.sol` | `0x06036B53A1f8d2Cf691a6f324C0672eB6D865667` | `0x06036B53A1f8d2Cf691a6f324C0672eB6D865667` |

Deploy: `npx hardhat run scripts/deploy_audit_log.js --network arbitrumSepolia`

---

## Environment variables

| Variable | Used by |
|---|---|
| `CHAINS` | ingestion — comma-separated chain names (`mantle,arbitrum`) |
| `MANTLE_RPC_URL` | ingestion, contracts |
| `ARBITRUM_RPC_URL` | ingestion — defaults to public `arb1.arbitrum.io/rpc` |
| `ANTHROPIC_API_KEY` | enrichment — Claude Sonnet |
| `TELEGRAM_BOT_TOKEN` | delivery — Mantis Scout Telegram bot |
| `DISCORD_BOT_TOKEN` | delivery — Discord bot (optional) |
| `LINE_CHANNEL_ACCESS_TOKEN` / `LINE_CHANNEL_SECRET` | delivery — LINE bot (optional) |
| `BYREAL_PRIVATE_KEY` | executor — Execute agent wallet |
| `AUDIT_CONTRACT_ADDRESS` | delivery — SignalAuditLog.sol |
| `AGENT_IDENTITY_CONTRACT_ADDRESS` | executor — AgentIdentity.sol |
| `ZSCORE_THRESHOLD` | detection — global anomaly threshold (default `2.5`) |
| `ZSCORE_THRESHOLD_ARBITRUM` | detection — per-chain override (default `3.5`) |
| `NANSEN_API_KEY` | ingestion — wallet intelligence |
| `ELFA_API_KEY` | enrichment — protocol sentiment |

---

## Chain support

| Chain | Status | Protocols | Execution |
|---|---|---|---|
| Mantle | Live | Agni Finance, Merchant Moe, Fluxion | Scout + Execute |
| Arbitrum | Live | Uniswap V3, Trader Joe | Scout only (Execute: phase 2) |

Adding a new chain: add a `ChainConfig` entry in `packages/ingestion/src/chains.py`, set `CHAINS=mantle,arbitrum,<new>` — no other code changes needed.

---

## Scripts

```bash
# Check RPC health for all enabled chains
python scripts/check_rpc_health.py --chain mantle,arbitrum

# Replay a historical block range and print decoded events
python scripts/replay_block_range.py --chain arbitrum --from 479900000 --to 479900500 --min-usd 100000
```

---

## Stack

Python 3.11 · Solidity 0.8.20 · web3.py · Claude Sonnet · Byreal Skills CLI ·
Nansen API · Elfa AI · Telegram Bot API · Discord API · LINE Messaging API ·
Postgres · Redis · Hardhat · Docker · Mantle L2 · Arbitrum L2
