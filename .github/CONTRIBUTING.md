# Contributing to Mantis

## Branch conventions
- `main` — stable, demo-ready code only
- `feat/<name>` — new features (e.g. `feat/elfa-sentiment`)
- `fix/<name>` — bug fixes

## Commit message format
```
feat: add Elfa sentiment enrichment to signal classifier
fix: handle zero-std baseline in z-score detector
chore: update hardhat to 2.22
```

## Week 1 priorities (Jun 9–15)
1. Deploy SignalAuditLog.sol to Mantle Sepolia
2. Deploy AgentIdentity.sol to Mantle Sepolia
3. Scaffold Postgres schema (run migrations)
4. Confirm Byreal CLI: `npx byreal-cli pool-query --network mantle`
5. Post launch X thread with this repo link

## Week 2 priorities (Jun 16–22)
1. `packages/ingestion` — live Mantle RPC event poller
2. `packages/detection` — z-score pipeline on real data
3. `packages/enrichment` — Claude Sonnet LLM call + JSON parse
4. `packages/delivery/telegram` — bot responds to /subscribe

## Never commit
- Private keys or seed phrases
- .env files
- Contract deployment receipts with real addresses (use .gitignore)
