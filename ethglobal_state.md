# Mantis — ETHGlobal Tokyo Build State

Last updated: 2026-09-26

## Current status

**Stage:** Code implementation complete; sandbox verification and submission work remain.

**Overall readiness:** World demo-ready; ETHGlobal submission assets and partner forms remain.

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
- [x] Complete one real successful verification.
- [x] Complete one real denied/expired/mismatched verification.

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
- [x] Run live World sandbox verification (OIDC callback returned `ok: true` and a signed artifact).
- [x] Run live blocked demo paths (expired and intent-mismatch artifacts).

## Sponsor submission checklist

### World

- [x] Backend validation design documented.
- [x] Failure path implemented.
- [x] Verification request shown in demo.
- [x] Human completion shown in demo.
- [x] Validated result shown in demo.
- [x] Protected action shown in demo (executor returned `status: success` with `approval_status: approved`; venue remains dry-run).
- [x] Integration debrief completed with time-to-first-success, friction, and improvement.
- [ ] Submit prize entry.

### Uniswap

- [x] Uniswap integration exists in the repository.
- [x] `FEEDBACK.md` exists.
- [x] Live approved dry-run swap demonstrated; real-money execution remains disabled.
- [ ] Developer feedback form submitted.
- [ ] Submit prize entry.

### Curvegrid

- [x] Summary and architecture documented.
- [x] Setup/testing documented.
- [ ] Team/social links finalized.
- [ ] Submit prize entry.

## Known blockers

1. Uniswap live/testnet approved swap evidence is still pending; current protected execution evidence is dry-run.
2. Final ETHGlobal assets (logo, banner, screenshots, video) and sponsor form submissions are external actions still pending.

## Next actions

1. Record the 2–4 minute demo covering World approval, approved dry-run, expiry rejection, and intent mismatch.
2. Finish ETHGlobal team/social metadata, logo, banner, and three screenshots.
3. Submit the World entry and complete the Uniswap Developer Feedback Form.
4. Submit the final ETHGlobal project before the deadline.

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
