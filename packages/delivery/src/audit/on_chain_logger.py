"""
OnChainLogger
─────────────
Calls SignalAuditLog.logSignal() on Mantle immediately after a signal
is dispatched via Telegram. Returns the tx hash, which is stored back
on the signal row in Postgres so users can verify on the explorer.

Hash format (matches the Solidity contract's verify() expectation):
  keccak256(abi.encodePacked(canonicalJSON))

where canonicalJSON has alphabetically-ordered keys:
  {
    "confidence":  <int>,
    "deliver_at":  "<ISO-8601 UTC>",
    "id":          <int>,
    "pool":        "<checksummed address>",
    "protocol":    "<string>",
    "signal_type": "<string>",
    "summary":     "<string>"
  }

IMPORTANT: key order MUST be stable. Python dicts preserve insertion
order (3.7+), so always build the dict in this order.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

from eth_account import Account
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

log = logging.getLogger(__name__)

# ── ABI fragment (only the functions we call) ────────────────────────────────
_ABI = [
    {
        "inputs": [
            {"internalType": "bytes32",      "name": "signalHash",      "type": "bytes32"},
            {"internalType": "string",       "name": "protocol",        "type": "string"},
            {"internalType": "string",       "name": "signalType",      "type": "string"},
            {"internalType": "uint8",        "name": "confidenceScore", "type": "uint8"},
        ],
        "name": "logSignal",
        "outputs": [{"internalType": "uint256", "name": "signalId", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "signalId",   "type": "uint256"},
            {"internalType": "string",  "name": "payloadJSON", "type": "string"},
        ],
        "name": "verify",
        "outputs": [
            {"internalType": "bool",    "name": "valid",    "type": "bool"},
            {"internalType": "uint256", "name": "storedAt", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "totalSignals",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]


@dataclass
class AuditResult:
    signal_id:  int
    tx_hash:    str
    block:      int
    gas_used:   int
    explorer_url: str


class OnChainLogger:
    """
    Thread-safe wrapper around SignalAuditLog.sol.
    Instantiate once and reuse across the delivery worker.
    """

    def __init__(
        self,
        rpc_url:          str  = "",
        contract_address: str  = "",
        private_key:      str  = "",
    ):
        rpc_url          = rpc_url          or os.environ["MANTLE_RPC_URL"]
        contract_address = contract_address or os.environ["AUDIT_CONTRACT_ADDRESS"]
        private_key      = private_key      or os.environ["DEPLOYER_PRIVATE_KEY"]

        self._w3 = Web3(Web3.HTTPProvider(rpc_url))
        # Mantle uses PoA-compatible block headers
        self._w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        self._account  = Account.from_key(private_key)
        self._contract = self._w3.eth.contract(
            address=Web3.to_checksum_address(contract_address),
            abi=_ABI,
        )
        self._chain_id = self._w3.eth.chain_id

        explorer_base = (
            "https://explorer.mantle.xyz"
            if self._chain_id == 5000
            else "https://explorer.sepolia.mantle.xyz"
        )
        self._explorer_tx  = f"{explorer_base}/tx"
        self._explorer_addr = f"{explorer_base}/address/{contract_address}"

        log.info(
            "OnChainLogger ready — contract=%s chain=%s",
            contract_address[:10], self._chain_id,
        )

    # ── Public API ───────────────────────────────────────────────────────────

    def log_signal(self, signal) -> Optional[AuditResult]:
        """
        Hash and log a signal to SignalAuditLog.sol.

        Args:
            signal: object with fields matching the canonical JSON schema
                    (id, protocol, pool_address, signal_type, confidence,
                     summary, deliver_at)

        Returns:
            AuditResult with tx_hash and on-chain signalId,
            or None if the transaction fails.
        """
        payload   = self._canonical_json(signal)
        sig_hash  = self._hash_payload(payload)

        try:
            tx_hash, receipt = self._send_tx(
                self._contract.functions.logSignal(
                    sig_hash,
                    signal.cluster.protocol.value,
                    signal.signal_type.value,
                    min(int(signal.confidence), 100),
                )
            )
        except Exception as exc:
            log.error("logSignal tx failed: %s", exc)
            return None

        on_chain_id = self._parse_signal_id(receipt)
        result = AuditResult(
            signal_id   = on_chain_id,
            tx_hash     = tx_hash,
            block       = receipt["blockNumber"],
            gas_used    = receipt["gasUsed"],
            explorer_url= f"{self._explorer_tx}/{tx_hash}",
        )
        log.info(
            "Signal logged on-chain: id=%s tx=%s gas=%s",
            on_chain_id, tx_hash[:14], receipt["gasUsed"],
        )
        return result

    def verify(self, signal_id: int, payload_json: str) -> tuple[bool, int]:
        """
        Call verify() on the contract. Returns (is_valid, stored_at_timestamp).
        Useful for the Telegram /verify command.
        """
        return self._contract.functions.verify(signal_id, payload_json).call()

    def total_signals(self) -> int:
        return self._contract.functions.totalSignals().call()

    @property
    def contract_url(self) -> str:
        return self._explorer_addr

    # ── Private helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _canonical_json(signal) -> str:
        """
        Build the canonical JSON string with alphabetically sorted keys.
        This MUST match what's passed to verify() — any deviation breaks
        hash verification.
        """
        return json.dumps({
            "confidence":  int(signal.confidence),
            "deliver_at":  signal.deliver_at.isoformat(),
            "id":          int(signal.id),
            "pool":        Web3.to_checksum_address(signal.cluster.pool_address),
            "protocol":    signal.cluster.protocol.value,
            "signal_type": signal.signal_type.value,
            "summary":     signal.summary,
        }, separators=(",", ":"))   # compact — no spaces

    @staticmethod
    def _hash_payload(payload: str) -> bytes:
        """keccak256 of UTF-8 encoded payload (matches Solidity abi.encodePacked)."""
        return Web3.keccak(text=payload)

    def _send_tx(self, fn) -> tuple[str, dict]:
        """Build, sign, send, and wait for a transaction. Returns (tx_hash, receipt)."""
        nonce    = self._w3.eth.get_transaction_count(self._account.address)
        gas_est  = fn.estimate_gas({"from": self._account.address})
        gas_price = self._w3.eth.gas_price

        tx = fn.build_transaction({
            "from":     self._account.address,
            "nonce":    nonce,
            "gas":      int(gas_est * 1.2),   # 20% buffer
            "gasPrice": gas_price,
            "chainId":  self._chain_id,
        })

        signed  = self._w3.eth.account.sign_transaction(tx, self._account.key)
        tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

        if receipt["status"] != 1:
            raise RuntimeError(f"Transaction reverted: {tx_hash.hex()}")

        return tx_hash.hex(), receipt

    @staticmethod
    def _parse_signal_id(receipt: dict) -> int:
        """
        Extract the returned signalId from the first SignalLogged event log.
        Falls back to 0 if the log can't be parsed (shouldn't happen).
        """
        try:
            # SignalLogged topic[1] = signalId (indexed)
            return int(receipt["logs"][0]["topics"][1].hex(), 16)
        except (IndexError, KeyError):
            log.warning("Could not parse signalId from receipt")
            return 0
