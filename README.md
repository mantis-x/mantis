# Mantis

> See the move. Make the move.

Signal-to-execution AI system built entirely on Mantle.
Competing in **Track 2 (AI Alpha & Data)** + **Track 6 (Agentic Economy)**
at the Mantle Turing Test Hackathon 2026.

---

## What Mantis is

**Mantis Scout** is the intelligence layer. An AI agent that monitors Mantle
DeFi protocols — Agni Finance, Merchant Moe, Fluxion — around the clock.
It detects smart money wallet clusters using z-score anomaly detection,
enriches each signal through Claude Sonnet, and delivers plain-English alerts
with confidence scores directly to Telegram. Every signal is hashed and
recorded immutably on-chain via `SignalAuditLog.sol` — fully auditable,
forever.

**Mantis Execute** is the execution layer. An intent-based agentic wallet
powered by the Byreal Skills CLI. You define your intent once — "follow smart
money into mETH pools when confidence exceeds 75" — and the agent evaluates
safety guards, sizes the position, and executes autonomously. Every decision,
including aborts, is logged to the agent's ERC-8004 on-chain identity.

Both products share one backend pipeline. Scout is the human interface.
Execute is the autonomous execution layer. Together: **signal detected →
agent triggered → position opened → outcome logged on Mantle**.

---

## Monorepo structure

```
mantis/
├── packages/
│   ├── shared/      # DB models, signal queue, schemas — used by all packages
│   ├── ingestion/   # Mantle RPC poller + Agni / Merchant Moe / Fluxion decoders
│   ├── detection/   # Z-score anomaly detection + wallet clustering
│   ├── enrichment/  # LLM signal enrichment via Claude Sonnet
│   ├── delivery/    # Telegram bot + on-chain audit logger  (Mantis Scout)
│   └── executor/    # Intent engine + Byreal execution + ERC-8004  (Mantis Execute)
├── contracts/       # Solidity: SignalAuditLog.sol + AgentIdentity.sol
├── scripts/         # Dev utilities: backtest, replay, health checks
└── docs/            # Architecture, API reference, demo day runbook
```

---

## Quick start

```bash
cp .env.example .env        # fill in API keys
docker-compose up -d        # start Postgres + Redis
make migrate                # run DB migrations
make ingest                 # start ingestion worker
make detect                 # start detection + enrichment
make deliver                # start Mantis Scout Telegram bot
make execute                # start Mantis Execute agent
```

---

## Contracts (Mantle)

| Contract | Purpose |
|---|---|
| `SignalAuditLog.sol` | Immutable keccak256 hash log of every Scout signal |
| `AgentIdentity.sol` | ERC-8004 on-chain reputation ledger for Execute agents |

Deployed addresses added to `.env` after `make contracts`.

---

## Environment variables

See `.env.example` for all required keys:

| Variable | Used by |
|---|---|
| `MANTLE_RPC_URL` | ingestion, contracts |
| `NANSEN_API_KEY` | ingestion — wallet intelligence |
| `ELFA_API_KEY` | enrichment — protocol sentiment |
| `ANTHROPIC_API_KEY` | enrichment — Claude Sonnet |
| `TELEGRAM_BOT_TOKEN` | delivery — Mantis Scout bot |
| `BYREAL_PRIVATE_KEY` | executor — Mantis Execute agent wallet |
| `AUDIT_CONTRACT_ADDRESS` | delivery — SignalAuditLog.sol |
| `AGENT_IDENTITY_CONTRACT_ADDRESS` | executor — AgentIdentity.sol |

---

## Track alignment

| Layer | Package | Track |
|---|---|---|
| Data ingestion | `ingestion/` | Shared |
| Anomaly detection | `detection/` | Shared |
| LLM enrichment | `enrichment/` | Shared |
| Signal queue (5-min delay) | `shared/queue/` | Shared |
| Telegram delivery | `delivery/telegram/` | Track 2 — Mantis Scout |
| On-chain audit log | `delivery/audit/` | Track 2 — Mantis Scout |
| Intent rule engine | `executor/intent/` | Track 6 — Mantis Execute |
| Safety guards | `executor/guards/` | Track 6 — Mantis Execute |
| Byreal Skills execution | `executor/byreal/` | Track 6 — Mantis Execute |
| ERC-8004 identity | `executor/identity/` | Track 6 — Mantis Execute |

---

## Stack

Python 3.12 · Solidity 0.8.20 · web3.py · Claude Sonnet · Byreal Skills CLI ·
Nansen API · Elfa AI · Telegram Bot API · Postgres · Redis · Hardhat · Docker

---

## Demo Day — July 2, 2026

Live demo: signal detected on Mantle → Mantis Execute agent acts → both
contracts updated → full loop verified on Mantle explorer in under 90 seconds.

Backup recording available if network is unstable.

---

*Mantis — Mantle Turing Test Hackathon 2026*
