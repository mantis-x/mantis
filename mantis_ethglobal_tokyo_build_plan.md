# Mantis — ETHGlobal Tokyo 2026 Build Plan

**Status:** Draft v1 · 2026-09-25
**Goal:** Extend Mantis (Scout + Execute + AgentIdentity) with a human-approval gate and a new execution venue, then submit the same build against **three** sponsor prizes: World, Uniswap Foundation, Curvegrid.

**Event:** ETHGlobal Tokyo 2026, Sep 25–27, 2026 (workshops today, submission Sun Sep 27 — confirm exact deadline time on the Info tab).

---

## 0. Context & prizes targeted

| Sponsor | Prize | Amount | Why Mantis fits |
|---|---|---|---|
| World | Best Use of World ID for Agents | $7,500 pool | Execute needs a human-approval/identity layer before it acts — that's the prize brief verbatim |
| Uniswap Foundation | Best Uniswap Stack Contribution | $6,000 pool ($3k/$2k/$1k) | Execute needs a real swap venue; Uniswap on Arbitrum is a natural fit |
| Curvegrid | Best AI Agent Project | $1,000 | Describes Scout+Execute almost exactly ("on-chain monitoring agent... surface unusual activity") |

**Note:** confirm on the Info/FAQ tab whether ETHGlobal Tokyo caps the number of sponsor prizes one project can apply for (some past events capped at 3) — three should be safe but verify before submission day.

## 1. Positioning — the unified narrative

> **Mantis — an AI agent that only acts with verified human consent, through a stack that enforces it.**

One new capability threads through all three prizes instead of three separate bolt-ons:

```
Scout detects anomaly                    (existing)
        ↓
Execute proposes a trade                 (existing)
        ↓
World ID for Agents: human verifies      🆕 approval gate
   → approve / deny / expire
        ↓
AgentIdentity logs the decision          (existing ERC-8004 contract)
        ↓
Trade executes via Uniswap on Arbitrum   🆕 new execution venue
        ↓
Curvegrid submission = package the whole loop as the "AI Agent Project"
```

Curvegrid costs almost nothing extra once the other two exist — it's a README describing the system you already built.

## 2. Architecture — what actually changes

- **Execute (existing):** add one gate before any trade above a threshold fires — a World ID for Agents verification call. Approved → proceed; denied/expired → abort, same as existing abort-logging behavior.
- **AgentIdentity (existing ERC-8004 contract):** no schema change needed — log the approval outcome as part of the existing decision record (approved / denied / expired / aborted).
- **Uniswap venue (new):** Execute routes the approved trade through Uniswap's router/SDK on Arbitrum, alongside (or instead of) the current Byreal Skills CLI path. Start with the v3 SDK — safest for the timeline. A v4 hook that checks AgentIdentity state on-chain before allowing the swap is a stronger story but higher risk; only attempt as a stretch goal.
- **chains.py registry:** no new chain needed — Arbitrum is already registered; this just adds a venue, not a chain.

## 3. Build timeline

### Tonight (Fri, post-workshops)
- [ ] Get World ID for Agents sandbox access (`sandbox.auth.world.org`) — start first, auth setup always has friction
- [ ] Confirm Uniswap SDK/access path — v3 SDK on Arbitrum (safe default)
- [ ] Decide scope: v3 swap integration (safe) vs. v4 hook (stretch, only if Day 2 goes fast)
- [ ] Re-read Execute's trade-firing code to find the exact insertion point for the approval gate

### Day 2 (Sat) — build day
- [ ] Wire World ID verification into Execute: trade over threshold → require completed World ID check before it proceeds
- [ ] Add Uniswap as an execution venue: Execute routes the approved trade through Uniswap's router/API on Arbitrum
- [ ] Confirm approval outcome + trade result write to AgentIdentity, same pattern as today
- [ ] Test both paths end to end: one trade approved and executed, one denied/expired and aborted
- [ ] Draft the three submission docs in parallel:
  - [ ] Curvegrid README (5 required sections: summary, MultiBaas usage if any, team, setup/testing, feedback)
  - [ ] Uniswap `FEEDBACK.md` + Uniswap Developer Feedback Form submission
  - [ ] World integration debrief (time-to-first-success, friction, one improvement suggestion)

### Day 3 (Sun) — submission day
- [ ] Record one demo video covering all three: Scout signal → World ID approval prompt → approved trade via Uniswap → AgentIdentity log entry → cut to a denied case that gets blocked
- [ ] Submit to World — Best Use of World ID for Agents
- [ ] Submit to Uniswap — Best Uniswap Stack Contribution (repo + FEEDBACK.md + feedback form link)
- [ ] Submit to Curvegrid — Best AI Agent Project (repo + README)
- [ ] Submit with buffer before the deadline — ETHGlobal's submission system gets slow near cutoff

## 4. Submission checklist (per sponsor)

**World:**
- [ ] Complete verification journey demoed: request → user completion → validated result → protected action
- [ ] One denied/expired/cancelled path shown, not just the happy path
- [ ] Identity result validated server-side, not trusted from client
- [ ] Integration debrief included

**Uniswap:**
- [ ] Public GitHub repo, open source
- [ ] `FEEDBACK.md` present
- [ ] Uniswap Developer Feedback Form submitted, linking to `FEEDBACK.md`
- [ ] README clearly points to the relevant contracts/lines of code

**Curvegrid:**
- [ ] Public GitHub repo with contracts/tests/docs
- [ ] README: one-sentence summary, MultiBaas usage (optional), team + socials, setup/testing instructions, MultiBaas feedback (if used)

## 5. Risks

| Risk | Mitigation |
|---|---|
| Exact submission deadline/time unconfirmed | Check Info tab today, don't assume |
| Sponsor-prize cap unknown | Confirm on FAQ before committing to all three |
| World ID sandbox is the one dependency outside your control | Start tonight, not tomorrow morning |
| v4 hook scope creep | Default to v3 SDK; only attempt hook if Day 2 finishes early |
| Time pressure across 3 submission packages | Draft README/FEEDBACK docs in parallel with build, not after |
