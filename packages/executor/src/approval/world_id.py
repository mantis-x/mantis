"""World ID for Agents approval gate.

The executor deliberately treats the client proof as untrusted input.  A proof
is accepted only after the configured World verification endpoint validates it
server-side.  The endpoint is configurable because the ETHGlobal sandbox and
production World environments use different credentials/URLs.

Expected proof payload is forwarded as JSON.  The verifier accepts the common
World response shapes (``success: true`` or ``verified: true``) and rejects
everything else, including transport errors.
"""
from __future__ import annotations

import logging
import os
import hashlib
import json
import base64
import hmac
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional

import requests

log = logging.getLogger(__name__)


class ApprovalStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    REQUIRED = "required"
    ERROR = "error"


@dataclass(frozen=True)
class ApprovalDecision:
    status: ApprovalStatus
    reason: str = ""
    verification_id: Optional[str] = None

    @property
    def approved(self) -> bool:
        return self.status in (ApprovalStatus.NOT_REQUIRED, ApprovalStatus.APPROVED)


class WorldIDVerifier:
    """Small HTTP adapter for World's server-side verification endpoint."""

    def __init__(
        self,
        verify_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        session: Any = None,
    ):
        self.verify_url = verify_url or os.getenv("WORLD_ID_VERIFY_URL", "")
        self.api_key = api_key if api_key is not None else os.getenv("WORLD_ID_API_KEY", "")
        self.timeout_seconds = timeout_seconds or float(os.getenv("WORLD_ID_VERIFY_TIMEOUT_SECONDS", "10"))
        self.session = session or requests.Session()

    def verify(
        self,
        proof: Mapping[str, Any],
        action: str,
        signal_hash: Optional[str] = None,
    ) -> ApprovalDecision:
        if not isinstance(proof, Mapping) or not proof:
            return ApprovalDecision(ApprovalStatus.DENIED, "missing World ID proof")

        artifact = proof.get("oidc_artifact")
        if artifact:
            return _verify_oidc_artifact(artifact, signal_hash)

        if not self.verify_url:
            return ApprovalDecision(
                ApprovalStatus.ERROR,
                "WORLD_ID_VERIFY_URL is not configured; refusing unverified approval",
            )

        payload = dict(proof)
        # Never trust action/signal binding supplied by the client. The
        # executor owns both values and World verifies the proof against them.
        payload["action"] = action
        if signal_hash:
            payload["signal_hash"] = signal_hash
        headers = {"Content-Type": "application/json", "User-Agent": "mantis-executor/1.0"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            response = self.session.post(
                self.verify_url,
                json=payload,
                headers=headers,
                timeout=self.timeout_seconds,
            )
            data = response.json() if response.content else {}
        except (requests.RequestException, ValueError) as exc:
            log.warning("World ID verification failed: %s", exc)
            return ApprovalDecision(ApprovalStatus.ERROR, f"World verification error: {exc}")

        if not isinstance(data, Mapping):
            return ApprovalDecision(
                ApprovalStatus.ERROR,
                "World returned a non-object verification response",
            )

        if response.status_code >= 400:
            reason = data.get("detail") or data.get("message") or "World rejected the proof"
            return ApprovalDecision(ApprovalStatus.DENIED, str(reason), _verification_id(data))

        verified = data.get("success") is True or data.get("verified") is True
        if not verified:
            return ApprovalDecision(ApprovalStatus.DENIED, "World response was not verified", _verification_id(data))
        return ApprovalDecision(ApprovalStatus.APPROVED, "World ID verified", _verification_id(data))


class WorldIDApprovalGate:
    """Require fresh World approval for executions above a USD threshold."""

    def __init__(self, verifier: Optional[WorldIDVerifier] = None, threshold_usd: Optional[float] = None):
        # $100 keeps ordinary small probes cheap while protecting the trades
        # that can materially move an agent wallet. Set to 0 to protect every
        # action in a demo or production policy.
        configured = os.getenv("WORLD_ID_APPROVAL_THRESHOLD_USD", "100")
        self.threshold_usd = float(configured) if threshold_usd is None else float(threshold_usd)
        self.verifier = verifier or WorldIDVerifier()

    def check(self, request) -> ApprovalDecision:
        if request.amount_usd < self.threshold_usd:
            return ApprovalDecision(ApprovalStatus.NOT_REQUIRED, "below World ID approval threshold")

        proof = getattr(request, "world_id_proof", None)
        if not proof:
            return ApprovalDecision(
                ApprovalStatus.REQUIRED,
                f"World ID approval required for trades >= ${self.threshold_usd:.2f}",
            )

        expires_at = getattr(request, "approval_expires_at", None)
        if expires_at:
            if isinstance(expires_at, str):
                try:
                    expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                except ValueError:
                    return ApprovalDecision(ApprovalStatus.DENIED, "invalid World ID approval expiry")
            if not isinstance(expires_at, datetime):
                return ApprovalDecision(ApprovalStatus.DENIED, "invalid World ID approval expiry")
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= datetime.now(timezone.utc):
                return ApprovalDecision(ApprovalStatus.EXPIRED, "World ID approval expired")

        intent_hash = _intent_hash(request)
        action = os.getenv("WORLD_ID_ACTION", "mantis_execute")
        return self.verifier.verify(proof, action=action, signal_hash=intent_hash)


def _intent_hash(request) -> str:
    """Hash the exact trade intent World approval authorizes."""
    intent = {
        "agent_id": request.agent_id,
        "signal_id": request.signal_id,
        "chain": request.chain,
        "action_type": request.action_type.value,
        "amount_usd": str(request.amount_usd),
        "max_slippage": str(request.max_slippage),
        "input_token": request.input_token or "",
        "output_token": request.output_token or "",
        "pool_address": request.pool_address,
    }
    encoded = json.dumps(intent, sort_keys=True, separators=(",", ":")).encode()
    return "0x" + hashlib.sha256(encoded).hexdigest()


def _verification_id(data: Mapping[str, Any]) -> Optional[str]:
    value = data.get("verification_id") or data.get("id") or data.get("nullifier_hash")
    return str(value) if value is not None else None


def _verify_oidc_artifact(artifact: Any, expected_intent_hash: Optional[str]) -> ApprovalDecision:
    """Validate the short-lived artifact issued by the Mantis OIDC callback."""
    secret = os.getenv("WORLD_ID_STATE_SECRET", "")
    if not secret or not isinstance(artifact, str):
        return ApprovalDecision(ApprovalStatus.ERROR, "OIDC approval secret is not configured")
    try:
        encoded, supplied = artifact.split(".", 1)
        expected = base64.urlsafe_b64encode(
            hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest()
        ).rstrip(b"=").decode()
        if not hmac.compare_digest(supplied, expected):
            return ApprovalDecision(ApprovalStatus.DENIED, "invalid OIDC approval signature")
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if not isinstance(payload, Mapping) or int(payload.get("exp", 0)) < int(time.time()):
            return ApprovalDecision(ApprovalStatus.EXPIRED, "OIDC approval expired")
        if expected_intent_hash and payload.get("intent_hash") != expected_intent_hash:
            return ApprovalDecision(ApprovalStatus.DENIED, "OIDC approval does not match this trade")
        subject = payload.get("sub")
        return ApprovalDecision(ApprovalStatus.APPROVED, "World ID OIDC verified", str(subject) if subject else None)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return ApprovalDecision(ApprovalStatus.DENIED, "malformed OIDC approval artifact")
