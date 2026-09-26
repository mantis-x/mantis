# World ID for Agents integration

Mantis protects high-value Execute actions with a server-side World ID check.
The flow is:

1. Scout/Execute creates an `ExecutionRequest` with the proposed signal and
   optional `world_id_proof`.
2. The human completes the World flow and the client returns the proof.
3. The OIDC callback exchanges the authorization code and validates the ID
   token server-side; client claims are never treated as authorization. The
   server binds `signal_hash` to the canonical chain/action/amount/token intent.
4. Only an approved, unexpired result reaches the safety-approved venue.
5. Every outcome is included in the `ExecutionResult` and the existing
   ERC-8004 decision log.

## OIDC demo flow

Start approval for a canonical trade-intent hash:

```text
GET https://api.mantis.baiq.tech/auth/world/login?signal_hash=<intent_hash>
```

World redirects to the registered callback:

```text
https://api.mantis.baiq.tech/auth/world/callback
```

The callback exchanges the authorization code with Client Secret Basic,
validates the ID token against World discovery/JWKS, checks the nonce, and
returns a short-lived signed artifact. Pass it into the execution request as:

```json
{
  "world_id_proof": {
    "oidc_artifact": "<artifact returned by /auth/world/callback>"
  }
}
```

The executor verifies the artifact signature, expiry, and exact intent hash
before allowing the venue call.

Configuration:

```dotenv
WORLD_ID_APPROVAL_THRESHOLD_USD=100
WORLD_ID_CLIENT_ID=...
WORLD_ID_CLIENT_SECRET=...
WORLD_ID_VERIFY_TIMEOUT_SECONDS=10
WORLD_ID_REDIRECT_URI=https://api.mantis.baiq.tech/auth/world/callback
WORLD_ID_ISSUER=https://sandbox.auth.world.org
WORLD_ID_STATE_SECRET=...
WORLD_ID_ARTIFACT_TTL_SECONDS=600
```

The exact sandbox URL and credential come from the ETHGlobal World portal. Do
not commit either credential. Set the threshold to `0` for a demo where every
trade must show the approval journey. The gate fails closed when the endpoint
is unset or unavailable.

Demo failure cases are first-class: omit the proof, submit a rejected proof, or
use an expired `approval_expires_at`; all three stop before a venue call.

## Integration debrief

### ETHGlobal World submission notes

- **Time to first successful verification:** Same build session; the first
  successful sandbox callback was reached after configuring the new World app,
  matching the exact HTTPS callback, and deploying the OIDC client.
- **Friction encountered:** The sandbox initially returned `invalid_request`
  until the authorization request included S256 PKCE. The callback also needed
  to handle denial/error responses without requiring an authorization code. The
  executor demo then exposed two useful integration details: artifacts are
  short-lived, and the approved artifact must use the exact canonical intent
  hash, including `signal_id`.
- **Missing capability or documentation:** A clearer sandbox error for missing
  PKCE and a first-party end-to-end test recipe connecting an approved OIDC
  artifact to an execution request would reduce integration time.
- **Highest-impact improvement:** Provide a documented, provider-supported
  approval test harness that returns a canonical intent example and makes the
  authorization requirements (PKCE, redirect URI, nonce, and token exchange)
  explicit.

Observed demo evidence:

- Successful callback returned `ok: true` and a signed artifact.
- Expired artifact was rejected before venue execution.
- Artifact bound to a different intent was rejected before venue execution.
