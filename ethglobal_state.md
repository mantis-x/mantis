# Mantis — ETHGlobal Tokyo Build State

Last updated: 2026-09-26

## Current status

**Stage:** Code implementation complete; sandbox verification and submission work remain.

**Overall readiness:** Code-ready, not submission-ready.

## Product narrative

Mantis detects on-chain anomalies, proposes a trade, requires verified human
consent for consequential actions, executes approved Arbitrum swaps through
Uniswap V3, and records every decision through AgentIdentity.

```text
Scout signal
  → Execute intent
  → World ID approval
  → safety guards
  → Uniswap V3 swap on Arbitrum
  → AgentIdentity decision log
```

## Implementation checklist

### World ID for Agents

- [x] Approval gate inserted before venue execution.
- [x] Configurable USD threshold via `WORLD_ID_APPROVAL_THRESHOLD_USD`.
- [x] Server-side verification adapter implemented.
- [x] Missing proof fails closed.
- [x] Denied proof fails closed.
- [x] Expired proof fails closed.
- [x] Malformed verifier responses fail closed.
- [x] Approval bound to a canonical full trade-intent hash.
- [x] OIDC login route added at `/auth/world/login`.
- [x] OIDC callback added at `/auth/world/callback` on the API service.
- [x] Authorization-code exchange uses Client Secret Basic.
- [x] ID token validation uses issuer/JWKS discovery and nonce checking.
- [x] Callback issues a short-lived signed approval artifact.
- [x] Approval status included in `ExecutionResult` and AgentIdentity detail.
- [x] Configure ETHGlobal World OIDC redirect URI as `https://api.mantis.baiq.tech/auth/world/callback`.
- [x] Connect the World ID OIDC client/request flow in the API and site.
- [ ] Complete one real successful verification.
- [ ] Complete one real denied/expired/cancelled verification.

### Uniswap Foundation

- [x] Arbitrum execution routes through Uniswap V3 SwapRouter02.
- [x] QuoterV2 used for live minimum-output protection.
- [x] Existing safety guards remain before execution.
- [x] Existing dry-run mode preserved.
- [x] `FEEDBACK.md` added.
- [ ] Run an approved end-to-end Arbitrum swap in the target environment.
- [ ] Submit the Uniswap Developer Feedback Form.
- [ ] Add the form/link details to the submission package.

### Curvegrid

- [x] Existing Scout + Execute agent narrative documented.
- [x] Curvegrid submission document added.
- [x] Setup/testing instructions documented.
- [x] MultiBaaS usage documented as not used.
- [ ] Add final individual team names and social links.
- [ ] Confirm public repository visibility.
- [ ] Submit to Curvegrid.

## Validation

- [x] Python compilation passes for executor source and tests.
- [x] Approval tests pass when executed directly.
- [x] Approved execution reaches the Uniswap venue mock.
- [x] Denied execution never reaches a venue.
- [x] `git diff --check` passes.
- [ ] Install/run full pytest suite; selected OIDC/executor tests pass, but API tests still need `fakeredis` locally.
- [ ] Run live World sandbox verification.
- [ ] Run live approved and blocked demo paths.

## Sponsor submission checklist

### World

- [x] Backend validation design documented.
- [x] Failure path implemented.
- [ ] Verification request shown in demo.
- [ ] Human completion shown in demo.
- [ ] Validated result shown in demo.
- [ ] Protected action shown in demo.
- [ ] Integration debrief completed with time-to-first-success, friction, and improvement.
- [ ] Submit prize entry.

### Uniswap

- [x] Uniswap integration exists in the repository.
- [x] `FEEDBACK.md` exists.
- [ ] Live approved swap demonstrated.
- [ ] Developer feedback form submitted.
- [ ] Submit prize entry.

### Curvegrid

- [x] Summary and architecture documented.
- [x] Setup/testing documented.
- [ ] Team/social links finalized.
- [ ] Submit prize entry.

## Known blockers

1. World sandbox credentials and exact verification endpoint are not configured.
2. No client-facing approval prompt is wired into a live demo flow yet.
3. Full pytest cannot run until pytest is installed in the environment.
4. Demo video and sponsor form submissions are external actions still pending.

## Next actions

1. Configure `WORLD_ID_CLIENT_ID`, `WORLD_ID_CLIENT_SECRET`, `WORLD_ID_STATE_SECRET`, and the API callback URI.
2. Update the World portal redirect URI to `https://api.mantis.baiq.tech/auth/world/callback`.
3. Run the World sandbox happy path and record the verification response shape.
4. Run denied/expired paths and confirm no Uniswap call occurs.
5. Run one approved Arbitrum dry-run/live-safe swap and capture AgentIdentity output.
6. Install pytest and run the full repository test suite.
7. Finish team/social metadata, developer feedback form, demo video, and submissions.

## Important configuration

```dotenv
WORLD_ID_APPROVAL_THRESHOLD_USD=100
WORLD_ID_CLIENT_ID=
WORLD_ID_CLIENT_SECRET=
WORLD_ID_REDIRECT_URI=https://api.mantis.baiq.tech/auth/world/callback
WORLD_ID_ISSUER=https://sandbox.auth.world.org
WORLD_ID_STATE_SECRET=
WORLD_ID_ARTIFACT_TTL_SECONDS=600
BYREAL_DRY_RUN=true
```

Keep `BYREAL_DRY_RUN=true` until sandbox verification, key custody, RPC health,
and the live execution approval are explicitly reviewed.
