# Reducing Railway Costs for Mantis

_Created: 2026-08-22_

## Current Cost Breakdown (Aug 2026)

| Resource | Minutely | Rate | Cost | % of Total |
|----------|----------|------|------|------------|
| Memory | 33,612.83 GB·min | $0.000231/GB·min | **$7.78** | **95.3%** |
| CPU | 403.31 vCPU·min | $0.000463/vCPU·min | $0.1867 | 2.3% |
| Egress | 3.22 GB | $0.05/GB | $0.1610 | 2.0% |
| Volume | 8,838.13 GB·min | $0.00000347/GB·min | $0.0307 | 0.4% |
| **Total** | | | **$8.16** | |

**Key insight: Memory is 95% of the bill.** CPU, egress, and volume are negligible. Every optimization must target memory.

## Why Memory Is So High

The deployed container runs **8 Python processes** via supervisord:

| Process | Main Libraries Loaded | Typical Idle? |
|---------|-----------------------|---------------|
| `ingestion` | web3, aiohttp, redis, sqlalchemy, eth-abi | No — polling 4 chains continuously |
| `detection` | numpy, networkx, redis | No — processing every event |
| `enrichment` | anthropic, redis, requests | Mostly idle — waits for candidates |
| `delivery` | python-telegram-bot, discord.py, line-bot-sdk, web3, pillow, qrcode, redis | Mostly idle — waits for signals |
| `executor` | web3, redis, requests | Mostly idle — waits for signals |
| `tracking` | sqlalchemy, redis | Mostly idle — waits for signals |
| `api` | fastapi, uvicorn, sqlalchemy, redis, aiohttp | Low traffic |
| `webhooks` | fastapi, uvicorn, sqlalchemy, redis, aiohttp | Low traffic |

Each process loads its own copy of the Python interpreter (~30-50 MB) plus all imported libraries. Heavy offenders:

- **web3** — imported by 4 processes (ingestion, delivery, executor, shared). ~100-200 MB per copy.
- **sqlalchemy + psycopg2** — imported by 4 processes (tracking, api, webhooks, shared). ~50-80 MB per copy.
- **discord.py + line-bot-sdk** — imported by delivery even though both are **disabled** (no tokens set). ~30-50 MB wasted.
- **pillow + qrcode** — imported by delivery for QR generation that's rarely used. ~20-30 MB.

**Estimated per-process memory:** 150-400 MB each → 8 processes × ~250 MB avg = **~2 GB baseline**, matching the observed ~1.8 GB avg on the Railway chart (spikes to 3.6 GB during burst activity).

## Optimization Strategies

### Strategy 1: Merge Lightweight Workers (HIGH IMPACT, LOW RISK)

Several workers are idle most of the time and sit in a `brpop` loop waiting for Redis messages. They could share a process without contention.

**Proposed merges:**

| Merge | Rationale | Estimated Savings |
|-------|-----------|-------------------|
| `tracking` → `delivery` | Both consume from signal-related Redis queues. tracking just writes to Postgres — trivial overhead. | ~1 Python heap (~200-300 MB) |
| `webhooks` → `api` | webhooks is literally the same FastAPI app's webhook routes, run as a separate process for no clear reason. | ~1 Python heap (~200-300 MB) |
| `executor` → `detection` | Executor only fires when a signal arrives (rare). Detection already runs an async event loop. | ~1 Python heap (~200-300 MB) |

**Result:** 8 processes → 5 processes. Estimated memory reduction: **600-900 MB** (~30-40% of current cost).

**Risk:** Low. These workers don't share state or compete for resources. The main risk is a crash in one worker taking down its host — mitigated by supervisord's `autorestart=true`.

**Implementation:** Merge the `main()` logic of tracking into delivery's async task set, and webhooks into api's uvicorn server. Remove the standalone supervisor entries.

### Strategy 2: Lazy-Import Heavy Libraries (MEDIUM IMPACT, LOW RISK)

Defer importing packages until they're actually needed, so processes that don't use them don't pay the memory cost.

**Target libraries:**

| Library | Used By | Could Defer? |
|---------|---------|--------------|
| `discord.py` | delivery (disabled) | Yes — skip import entirely when `DISCORD_BOT_TOKEN` is unset |
| `line-bot-sdk` | delivery (disabled) | Yes — skip import entirely when LINE tokens are unset |
| `pillow` | delivery (QR codes) | Yes — import only when generating a QR code |
| `qrcode` | delivery (QR codes) | Yes — import only when generating a QR code |
| `web3` | delivery (audit logging) | Partially — OnChainLogger already lazy-imports, but `from web3 import Web3` may be at module level |
| `fastapi` | api + webhooks | No — needed at startup |

**Estimated savings:** ~100-200 MB if discord.py + line-bot-sdk are fully skipped in delivery.

**Risk:** Very low. These are already conditionally used — the imports just need to move inside the conditional blocks.

### Strategy 3: Set Railway Memory Limit (IMMEDIATE, ZERO CODE)

Check the Railway service settings for the `mantis` service. If no memory limit is set, Railway may allocate more than needed.

- **Action:** In Railway dashboard → Settings → Service → set a memory limit (e.g., 1 GB or 1.5 GB).
- **Effect:** Caps the maximum memory charge even if usage spikes.
- **Risk:** If the container genuinely needs more than the limit during spikes, it will be OOM-killed and restart. Monitor after setting.

### Strategy 4: Use `python:3.11-alpine` (LOW IMPACT, MEDIUM RISK)

Alpine images are ~50 MB vs ~150 MB for slim. Reduces image size and pull time, but doesn't significantly affect runtime memory.

- **Risk:** Some packages (numpy, psycopg2, web3) may need compilation flags or `musl`-compatible wheels. Test thoroughly.
- **Savings:** Marginal at runtime — mainly reduces deploy time.

### Strategy 5: Split Into Separate Railway Services (MEDIUM IMPACT, HIGH RISK)

Instead of one container with 8 processes, split into 2-3 Railway services:

| Service | Processes | Memory Need |
|---------|-----------|-------------|
| `mantis-ingest` | ingestion + detection | High (always polling) |
| `mantis-serve` | enrichment + delivery + tracking | Medium (mostly idle) |
| `mantis-api` | api + webhooks + executor | Low (low traffic) |

**Benefit:** Each service gets its own memory allocation. The API service might only need 256 MB, while ingestion might need 512 MB — more efficient than one 2 GB container.

**Risk:** Higher operational complexity. Requires inter-service networking (Redis/Postgres must be reachable from all services). Separate deployments, separate logs, separate scaling.

### Strategy 6: Reduce Redis Queue Overhead

The `delivery` worker fans out signals to 3 Redis queues (`mantis:signals`, `mantis:signals:exec`, `mantis:signals:tracking`). Each queue is capped (1000, 5000, 5000 entries). Reducing these caps slightly won't save much memory (each signal payload is ~1 KB), but it's free.

## Recommended Implementation Order

1. **Immediate (no code):** Set Railway memory limit (#3)
2. **Quick win (low risk):** Lazy-import discord.py + line-bot-sdk in delivery (#2)
3. **High impact:** Merge tracking → delivery, webhooks → api, executor → detection (#1)
4. **If needed:** Split into separate Railway services (#5)

## Estimated Savings Summary

| Strategy | Memory Saved | Monthly Cost Saved | Effort |
|----------|-------------|-------------------|--------|
| Merge workers | ~600-900 MB | ~$3.00-4.50 | Medium |
| Lazy imports | ~100-200 MB | ~$0.50-1.00 | Low |
| Memory limit | Caps spike cost | Variable | Trivial |
| Alpine image | Marginal | ~$0.10 | Low |
| Split services | Better allocation | ~$1.00-2.00 | High |

**Combined optimistic estimate:** $4.50-7.50/month savings → **$0.66-3.66/month** (from $8.16 baseline).
