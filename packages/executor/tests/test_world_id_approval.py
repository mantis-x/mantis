import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.approval.world_id import ApprovalStatus, WorldIDApprovalGate, WorldIDVerifier
from src.models.execution_request import ActionType, ExecutionRequest


def request(**overrides):
    values = dict(
        agent_id=1, owner_wallet="0xabc", signal_id="42", signal_type="whale_entry",
        protocol="uniswap_v3", pool_address="0xpool", confidence=90, z_score=4.0,
        action_type=ActionType.SWAP, amount_usd=5000, max_slippage=.02, max_position=5,
    )
    values.update(overrides)
    return ExecutionRequest(**values)


def test_missing_proof_fails_closed():
    decision = WorldIDApprovalGate(threshold_usd=100).check(request())
    assert decision.status is ApprovalStatus.REQUIRED
    assert not decision.approved


def test_small_trade_does_not_need_approval():
    decision = WorldIDApprovalGate(threshold_usd=1000).check(request(amount_usd=10))
    assert decision.status is ApprovalStatus.NOT_REQUIRED
    assert decision.approved


def test_expired_proof_is_rejected_before_network_call():
    verifier = MagicMock()
    decision = WorldIDApprovalGate(verifier, threshold_usd=100).check(request(
        world_id_proof={"proof": "x"},
        approval_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    ))
    assert decision.status is ApprovalStatus.EXPIRED
    verifier.verify.assert_not_called()


def test_server_verified_proof_is_approved():
    verifier = MagicMock()
    verifier.verify.return_value = MagicMock(approved=True, status=ApprovalStatus.APPROVED)
    decision = WorldIDApprovalGate(verifier, threshold_usd=100).check(
        request(world_id_proof={"proof": "server-validated"})
    )
    assert decision.approved
    verifier.verify.assert_called_once()


def test_http_verifier_rejects_unverified_success_shape():
    session = MagicMock()
    session.post.return_value.status_code = 200
    session.post.return_value.content = b"{}"
    session.post.return_value.json.return_value = {"success": False}
    verifier = WorldIDVerifier("https://world.test/verify", session=session)
    decision = verifier.verify({"proof": "x"}, "mantis_execute:42:1")
    assert decision.status is ApprovalStatus.DENIED


def test_http_verifier_sends_proof_to_server():
    session = MagicMock()
    session.post.return_value.status_code = 200
    session.post.return_value.content = b'{"success": true, "id": "v-1"}'
    session.post.return_value.json.return_value = {"success": True, "id": "v-1"}
    verifier = WorldIDVerifier("https://world.test/verify", api_key="secret", session=session)
    decision = verifier.verify({"proof": "x"}, "mantis_execute:42:1")
    assert decision.status is ApprovalStatus.APPROVED
    assert decision.verification_id == "v-1"
    kwargs = session.post.call_args.kwargs
    assert kwargs["json"]["action"] == "mantis_execute:42:1"
    assert kwargs["headers"]["Authorization"] == "Bearer secret"


def test_http_verifier_overwrites_client_action_and_binds_signal_hash():
    session = MagicMock()
    session.post.return_value.status_code = 200
    session.post.return_value.content = b'{"success": true}'
    session.post.return_value.json.return_value = {"success": True}
    verifier = WorldIDVerifier("https://world.test/verify", session=session)
    verifier.verify(
        {"proof": "x", "action": "attacker-chosen", "signal_hash": "attacker-chosen"},
        "server-action",
        signal_hash="0xintent",
    )
    payload = session.post.call_args.kwargs["json"]
    assert payload["action"] == "server-action"
    assert payload["signal_hash"] == "0xintent"


def test_http_verifier_fails_closed_on_json_scalar():
    session = MagicMock()
    session.post.return_value.status_code = 200
    session.post.return_value.content = b'[]'
    session.post.return_value.json.return_value = []
    verifier = WorldIDVerifier("https://world.test/verify", session=session)
    decision = verifier.verify({"proof": "x"}, "action")
    assert decision.status is ApprovalStatus.ERROR


def test_malformed_expiry_fails_closed():
    verifier = MagicMock()
    decision = WorldIDApprovalGate(verifier, threshold_usd=100).check(
        request(world_id_proof={"proof": "x"}, approval_expires_at=object())
    )
    assert decision.status is ApprovalStatus.DENIED
    verifier.verify.assert_not_called()


def test_oidc_artifact_must_match_trade(monkeypatch):
    import base64, hashlib, hmac, json, time
    secret = "test-secret"
    monkeypatch.setenv("WORLD_ID_STATE_SECRET", secret)
    payload = {"sub": "human-1", "intent_hash": "wrong", "exp": int(time.time()) + 60}
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    signature = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    decision = WorldIDVerifier().verify(
        {"oidc_artifact": f"{encoded}.{signature}"}, "action", signal_hash="expected"
    )
    assert decision.status is ApprovalStatus.DENIED
