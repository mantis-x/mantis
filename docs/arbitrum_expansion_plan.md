# Mantis Multi-Chain Strategy — Arbitrum Expansion

**Status:** Draft v1 · 2026-07-02
**Goal:** Run Mantis Scout on Mantle **and** Arbitrum from a single deployment (one Railway service / one docker-compose stack), with a clean chain-abstraction layer so chain #3 is config, not code.

---

## 1. Guiding decisions

| Decision | Choice | Rationale |
|---|---|---|
| Deployment topology | One deployment, N collectors | User requirement. Ingestion worker spawns one asyncio collector task per enabled chain; detection/enrichment/delivery stay single-instance and chain-aware. |
| Arbitrum scope (phase 1) | **Scout only** (signals + alerts) | Mantis Execute is built on Byreal Skills CLI + ERC-8004 on Mantle. Arbitrum execution is a later phase via a generic Uniswap router adapter. |
| On-chain audit proof | **Stays on Mantle** (home chain) | Arbitrum signals are logged to the existing `SignalAuditLog` on Mantle with a `chain` field in the canonical payload. One contract, one deployer key, cheap gas, keeps the hackathon's Mantle-native story. Per-chain deployment stays possible later (hardhat network entry added but not required). |
| Queue topology | Shared Redis queues, `chain` field on every payload | One detection worker handles all chains; baselines are keyed by `(chain, pool)`. Avoids per-chain worker sprawl. |
| Chain config | Central registry in `packages/shared`, selected via `CHAINS=mantle,arbitrum` env var | Everything chain-specific (RPC, chain_id, explorer, PoA flag, poll cadence, pool registry, token prices) lives in one place. |

---

## 2. Current chain coupling (audit)

The pipeline is Mantle-hardcoded at these points:

| Layer | File | Coupling |
|---|---|---|
| Ingestion | `packages/ingestion/src/collectors/mantle_rpc.py` | `MantleRPCCollector` class; hardcoded `AGNI_POOLS` / `MERCHANT_MOE_POOLS` / `FLUXION_POOLS`; PoA middleware always injected; `TOKEN_PRICES` (MNT-centric); `MANTLE_RPC_URL`; 50-block batch cap tuned for Mantle's ~2s blocks |
| Ingestion | `packages/ingestion/src/decoders/event_normaliser.py` | `SWAP_TOPIC` is Agni's V3 variant (9-field Swap event incl. protocol fees); `_estimate_usd()` uses MNT price as proxy + assumes 18 decimals |
| Ingestion | `packages/ingestion/src/models/raw_event.py` | `Protocol` enum only has Mantle protocols; `NormalisedEvent` has no chain field |
| Detection | `packages/detection/src/detector.py`, `baselines/pool_baseline.py` | Baselines keyed by pool address only; synthetic seed history is Mantle pools; single `mantis:raw_events` queue with no chain dimension |
| Detection | `packages/detection/src/models/candidate.py` | `AnomalyCandidate.to_dict()` has no `chain` key |
| Enrichment | `packages/enrichment/src/prompts/signal_classifier.py` | Prompt says "Mantle DeFi" |
| Delivery | `packages/delivery/src/audit/on_chain_logger.py` | Explorer URL chosen by `chain_id == 5000`; canonical JSON has no `chain` key |
| Delivery | `packages/delivery/src/formatters/signal_card.py`, `telegram/commands.py`, `discord/commands.py`, `line/commands.py` | Mantle explorer links, Mantle copy |
| Executor | `packages/executor/src/executor.py` | Hardcoded WMNT/USDT Mantle token addresses; Byreal is Mantle-only (fine — phase 1 keeps execution Mantle-only, but executor must *filter by chain*) |
| Contracts | `contracts/hardhat.config.js` | Only `mantle` / `mantleSepolia` networks |
| Config/ops | `.env.example`, `docker-compose.yml`, `supervisord.conf`, `scripts/check_rpc_health.py`, `scripts/replay_block_range.py` | Single `MANTLE_RPC_URL` / `CHAIN_ID`; one ingestion process (kept — it multiplexes chains internally) |

---

## 3. Target architecture

```
                ┌─ ChainCollector(mantle)   ─┐
ingestion ──────┤                            ├──► Redis mantis:raw_events   (payload += chain)
 (1 process)    └─ ChainCollector(arbitrum) ─┘
                                                        │
detection (1 process) — baselines keyed (chain, pool) ──► mantis:anomaly_candidates (+= chain)
                                                        │
enrichment (Claude, chain-aware prompt) ────────────────► mantis:signals (+= chain)
                                                        │
        ┌───────────────────────────────────────────────┤
delivery: chain badge + per-chain explorer links    executor: processes chain=="mantle" only (phase 1)
audit:   SignalAuditLog on Mantle, payload has "chain"
```

### 3.1 Chain registry — `packages/shared/src/chains.py` (new)

Single source of truth, consumed by every package:

```python
@dataclass(frozen=True)
class ChainConfig:
    name: str                  # "mantle" | "arbitrum"
    chain_id: int              # 5000 | 42161
    rpc_env: str               # "MANTLE_RPC_URL" | "ARBITRUM_RPC_URL"
    default_rpc: str
    explorer_base: str         # https://explorer.mantle.xyz | https://arbiscan.io
    poa_middleware: bool       # True for Mantle, False for Arbitrum
    poll_interval_s: int       # 15 for Mantle, 10 for Arbitrum
    max_blocks_per_batch: int  # 50 for Mantle, 500 for Arbitrum (~4 blocks/s)
    native_token: str          # "mnt" | "eth"
    pool_registry: dict[str, ProtocolPool]   # address -> (protocol, decoder, token meta)
    token_prices: dict[str, float]           # fallback price cache

ENABLED_CHAINS = [c for c in ALL_CHAINS if c.name in os.getenv("CHAINS", "mantle").split(",")]
```

Note: `packages/shared` is currently mostly empty stubs and packages import via their own `src.` roots — the pragmatic option is to vendor `chains.py` into each package Docker context the same way existing shared code is handled, or add `packages/shared` to `PYTHONPATH` in the Dockerfiles. Decide during implementation; do not block on a packaging refactor.

### 3.2 Ingestion

- Generalise `MantleRPCCollector` → `ChainCollector(config: ChainConfig, ...)` in a new `collectors/evm_rpc.py`. Keep `mantle_rpc.py` as a thin alias for backward compatibility with tests/scripts.
- PoA middleware injected only when `config.poa_middleware`.
- Batch cap and poll interval come from config (Arbitrum produces ~4 blocks/s; 50-block cap at 15s polling would fall behind permanently).
- Worker (`packages/ingestion/src/worker.py`) starts one collector task per enabled chain: `asyncio.gather(*[c.run() for c in collectors])`.
- Every Redis payload gains `"chain": config.name` and `"chain_id": config.chain_id`.
- Dedup key becomes `f"{chain}:{tx_hash}:{log_index}"`.

**Arbitrum protocol targets (phase 1):**

| Protocol | Type | Decoder | Notes |
|---|---|---|---|
| Uniswap V3 | Concentrated liquidity | **New** `_decode_univ3_swap` | Canonical V3 `Swap` topic `0xc42079f9…` with 5-field data `(int256,int256,uint160,uint128,int24)` — **different from Agni's 7-field variant**. Mint/Burn topics are shared with Agni (identical event signatures). |
| Camelot (Algebra) | CL DEX | Phase 2 | Algebra Swap event differs again; defer. |
| Trader Joe LB | Liquidity Book | Reuse `_decode_lb_swap` | Merchant Moe is a Trader Joe fork — same `LB_SWAP_TOPIC`. |

Seed pool registry with the top ~6 Arbitrum pools by TVL (WETH/USDC 0.05%, WBTC/WETH, ARB/WETH, USDC/USDT on Uniswap V3; 1–2 Trader Joe LB pairs). **Addresses must be verified from arbiscan.io at implementation time — do not trust memory.** Add `Protocol.UNISWAP_V3` and `Protocol.TRADER_JOE` to the enum in `raw_event.py`.

**USD estimation fix (required for Arbitrum, improves Mantle too):** `_estimate_usd` currently multiplies raw 18-decimal amounts by the MNT price. On Arbitrum that's wrong on both axes (native = ETH, USDC/USDT are 6 decimals, WBTC is 8). Extend the pool registry entries with `(token0, token1, decimals0, decimals1)` and price the larger leg with the correct token/decimals from `config.token_prices`. This is the highest-risk correctness item — bad USD values poison z-scores downstream.

### 3.3 Detection

- `BaselineStore` keys become `(chain, pool_address, event_type)`.
- `_DictEvent` picks up `chain` from the payload (default `"mantle"` for backward compat with old queue entries).
- `_build_synthetic_history()` gains Arbitrum entries with Arbitrum-scale volumes (Uniswap V3 WETH/USDC does ~$1–5M/hour, ~2 orders of magnitude above Agni pools — without separate baselines every Arbitrum swap would look anomalous, or thresholds tuned for Arbitrum would silence Mantle).
- Optional per-chain override: `ZSCORE_THRESHOLD_ARBITRUM` env var, falling back to global.
- `AnomalyCandidate.to_dict()` and `ScoredEvent` gain `chain`.

### 3.4 Enrichment

- Signal payload carries `chain` through.
- `signal_classifier.py` prompt: replace "Mantle DeFi" framing with a chain-parameterised context line (protocol names + chain name), so Claude's summaries say the right chain.

### 3.5 Delivery

- `signal_card.py` + Telegram/Discord/LINE formatters: chain badge (e.g. `⛓ Arbitrum`) and per-chain explorer links from `ChainConfig.explorer_base` (tx/address URL patterns differ: mantlescan uses `/tx/`, arbiscan uses `/tx/` too — keep a small URL helper in `chains.py`).
- Subscription commands: optional `/subscribe arbitrum` chain filter (phase 2 nice-to-have; phase 1 delivers all chains to all subscribers).
- `OnChainLogger` (audit): unchanged target chain — still writes to Mantle. Canonical JSON gains a `"chain"` key (alphabetical ordering puts it first; the contract hashes the string opaquely, so no Solidity change needed). Explorer link in the *audit proof* stays Mantle; the *signal* links point at the signal's own chain.

### 3.6 Executor (phase 1 guard)

- `worker.py` skips signals where `chain != "mantle"` with an explicit log line ("Arbitrum execution not yet supported — signal alert-only").
- Phase 3 (post-hackathon roadmap): `ArbitrumSwapExecutor` using Uniswap `SwapRouter02` via web3.py behind the same `ExecutionRequest/Result` models and guard pipeline; ERC-8004 decision logging stays on Mantle.

### 3.7 Contracts & ops

- `contracts/hardhat.config.js`: add `arbitrum` (42161) and `arbitrumSepolia` (421614) network entries (unused in phase 1; enables later per-chain audit deployment).
- `.env.example`: add `ARBITRUM_RPC_URL=https://arb1.arbitrum.io/rpc`, `CHAINS=mantle,arbitrum`; deprecate the single `CHAIN_ID` in favour of the registry.
- `docker-compose.yml` / `supervisord.conf` / `railway.toml`: **no changes** — the single ingestion process multiplexes chains, which is the whole point of "one deployment".
- `scripts/check_rpc_health.py` and `scripts/replay_block_range.py`: add `--chain` flag driven by the registry.
- RPC budget: public `arb1.arbitrum.io/rpc` rate-limits aggressively at 500-block `get_logs` spans. Recommend a free-tier Alchemy/Infura Arbitrum key for the deployed instance; the code path is identical (just the env var value).

---

## 4. Build phases

**Phase 0 — Chain abstraction refactor (no behaviour change)** *(~1 day)*
1. Add `chains.py` registry with the Mantle entry only.
2. Generalise collector to `ChainCollector`; worker iterates `ENABLED_CHAINS` (still just Mantle).
3. Thread `chain` field through: NormalisedEvent → Redis payloads → ScoredEvent/WalletCluster/AnomalyCandidate → signal → formatters → canonical audit JSON.
4. Key baselines by `(chain, pool)`.
5. All existing tests pass; run live against Mantle to confirm identical behaviour.

**Phase 1 — Arbitrum ingestion** *(~1–2 days)*
1. Verify + add Arbitrum pool registry (arbiscan), `Protocol.UNISWAP_V3` / `Protocol.TRADER_JOE`.
2. New canonical Uniswap V3 swap decoder (topic `0xc42079f9…`, 5-field layout); reuse Mint/Burn/LB decoders.
3. Token-aware USD estimation (decimals + per-token price from registry).
4. Arbitrum-tuned polling (interval 10s, batch cap 500, no PoA middleware).
5. Unit tests: real Arbitrum log fixtures for swap/mint/burn decode; registry tests.

**Phase 2 — Chain-aware signal path** *(~1 day)*
1. Arbitrum synthetic baselines + optional per-chain z-score threshold.
2. Enrichment prompt chain-parameterisation.
3. Signal cards with chain badge + arbiscan links across Telegram/Discord/LINE.
4. Executor chain guard (Mantle-only execution).
5. Audit payload `chain` key; hardhat network entries.

**Phase 3 — Later / roadmap (not in this build)**
- Arbitrum execution via Uniswap router adapter + guards.
- Camelot/GMX decoders; per-chain subscriptions; live price oracle replacing static `TOKEN_PRICES`.

---

## 5. Verification

1. **Unit:** `make test` (or per-package pytest) — new decoder fixtures from real arbiscan logs; baseline keying tests; canonical-JSON test asserting the `chain` key ordering.
2. **Replay:** `python scripts/replay_block_range.py --chain arbitrum --from <N> --to <N+500>` against a recent range containing known whale swaps; assert decoded USD values within sanity bounds.
3. **Live smoke (one deployment):** `CHAINS=mantle,arbitrum docker compose up` — confirm one ingestion process logs events from both chains, detector emits candidates tagged with both chains, Telegram card shows the right explorer link per chain, and the Mantle audit tx verifies via `/verify` with the new payload format.
4. **Regression:** run with `CHAINS=mantle` only and confirm output is byte-identical to pre-refactor behaviour (minus the added `chain` field).

## 6. Risks

- **USD mis-pricing** (decimals/native-token assumptions) silently corrupts z-scores — mitigated by token-aware pricing + replay sanity checks before enabling alerts.
- **Arbitrum event volume** (~100× Mantle) may flood Redis/detection — `ltrim` caps already bound memory; watch candidate rate and raise `ZSCORE_THRESHOLD_ARBITRUM` if noisy.
- **Public RPC rate limits** — use a keyed provider for the deployed instance.
- **Payload format change** breaks `verify()` for *pre-existing* on-chain signals if re-verified with new-format JSON — old signals verify with old payloads (stored off-chain per signal), so no migration needed; note it in `docs/smart_contracts.md`.
