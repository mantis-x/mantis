# MantleScan Alpha + AlphaExecutor

Mantle Turing Test Hackathon 2026 — Track 2 (AI Alpha & Data) + Track 6 (Agentic Economy)

## What this is

**MantleScan Alpha** detects smart money movements across Mantle DeFi protocols
(Merchant Moe, Agni Finance, Fluxion) and delivers plain-English alerts with
confidence scores via Telegram. Every signal is hashed and recorded on-chain.

**AlphaExecutor** listens to the same signal pipeline and autonomously executes
DeFi actions on Mantle using Byreal Skills CLI, guided by user-defined intent
rules. Each agent carries an ERC-8004 on-chain identity that logs its full
decision history.

Both products share one backend. Track 2 = the human interface. Track 6 = the
autonomous execution layer.

## Monorepo structure

```
mantis/
├── packages/
│   ├── shared/        # DB models, queue, schemas — used by all packages
│   ├── ingestion/     # Mantle RPC poller + event decoders
│   ├── detection/     # Z-score anomaly detection + wallet clustering
│   ├── enrichment/    # LLM signal enrichment via Claude Sonnet
│   ├── delivery/      # Telegram bot + on-chain audit logger (Track 2)
│   └── executor/      # Intent engine + Byreal execution + ERC-8004 (Track 6)
├── contracts/         # Solidity: SignalAuditLog + AgentIdentity
├── scripts/           # Dev utilities: backtest, replay, health checks
└── docs/              # Architecture, API reference, runbooks
```

## Quick start

```bash
cp .env.example .env        # fill in API keys
docker-compose up -d        # starts postgres + redis
make migrate                # run DB migrations
make ingest                 # start the ingestion worker
make detect                 # start detection + enrichment
make deliver                # start Telegram bot (Track 2)
make execute                # start AlphaExecutor (Track 6)
```

## Environment variables

See `.env.example` for all required keys:
- `MANTLE_RPC_URL` — Mantle mainnet or Sepolia RPC endpoint
- `NANSEN_API_KEY` — Nansen wallet intelligence API
- `ELFA_API_KEY` — Elfa AI sentiment API
- `ANTHROPIC_API_KEY` — Claude Sonnet for signal enrichment
- `TELEGRAM_BOT_TOKEN` — Telegram bot token
- `BYREAL_PRIVATE_KEY` — Executor agent wallet private key
- `AUDIT_CONTRACT_ADDRESS` — Deployed SignalAuditLog.sol address
- `AGENT_IDENTITY_CONTRACT_ADDRESS` — Deployed AgentIdentity.sol address

## Contracts (Mantle Sepolia testnet)

| Contract | Address | Purpose |
|---|---|---|
| SignalAuditLog | TBD after deploy | Immutable hash log of every signal |
| AgentIdentity | TBD after deploy | ERC-8004 agent decision history |

## Track alignment

| Layer | Package | Track |
|---|---|---|
| Data ingestion | `ingestion/` | Shared |
| Anomaly detection | `detection/` | Shared |
| LLM enrichment | `enrichment/` | Shared |
| Signal queue | `shared/queue/` | Shared |
| Telegram delivery | `delivery/telegram/` | Track 2 |
| On-chain audit | `delivery/audit/` | Track 2 |
| Intent rules | `executor/intent/` | Track 6 |
| Safety guards | `executor/guards/` | Track 6 |
| Byreal execution | `executor/byreal/` | Track 6 |
| ERC-8004 identity | `executor/identity/` | Track 6 |
