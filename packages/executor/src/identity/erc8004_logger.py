"""
ERC8004Logger — writes every agent decision to AgentIdentity.sol on the
agent's origin chain.

Every execution (success or abort) is logged on-chain:
  - What signal triggered it
  - What action was taken
  - Whether it succeeded or aborted (and why)

This forms the agent's verifiable reputation ledger — visible on Demo Day
by calling getDecision(agentId, index) on the origin chain's explorer.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

# ── Per-chain routing ─────────────────────────────────────────────────────────
# Contract addresses default to the shared AGENT_IDENTITY_CONTRACT_ADDRESS
# because the deployer wallet used the same nonce sequence on every chain so
# far, which makes the CREATE address identical across chains — but each
# chain gets its own override env var in case a future deploy diverges.
_CHAIN_RPC_ENV = {
    "mantle":   ("MANTLE_RPC_URL",   "https://rpc.mantle.xyz"),
    "arbitrum": ("ARBITRUM_RPC_URL", "https://arb1.arbitrum.io/rpc"),
    "hashkey":  ("HASHKEY_RPC_URL",  "https://mainnet.hsk.xyz"),
}
_CHAIN_CONTRACT_ENV = {
    "mantle":   "AGENT_IDENTITY_CONTRACT_ADDRESS",
    "arbitrum": "ARBITRUM_AGENT_IDENTITY_CONTRACT_ADDRESS",
    "hashkey":  "HASHKEY_AGENT_IDENTITY_CONTRACT_ADDRESS",
}
_CHAIN_EXPLORER = {
    "mantle":   "https://explorer.mantle.xyz",
    "arbitrum": "https://arbiscan.io",
    "hashkey":  "https://hsk.blockscout.com",
}

# AgentIdentity.sol ABI — only the functions we call
_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "agentOwner", "type": "address"},
            {"internalType": "string",  "name": "name",       "type": "string"},
        ],
        "name": "mintAgent",
        "outputs": [{"internalType": "uint256", "name": "agentId", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "agentId",    "type": "uint256"},
            {"internalType": "uint256", "name": "signalId",   "type": "uint256"},
            {"internalType": "string",  "name": "actionType", "type": "string"},
            {"internalType": "bool",    "name": "success",    "type": "bool"},
            {"internalType": "string",  "name": "detail",     "type": "string"},
        ],
        "name": "logDecision",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "uint256", "name": "agentId", "type": "uint256"}],
        "name": "decisionCount",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "uint256", "name": "agentId", "type": "uint256"}],
        "name": "agents",
        "outputs": [
            {"internalType": "address", "name": "owner",          "type": "address"},
            {"internalType": "string",  "name": "name",           "type": "string"},
            {"internalType": "uint256", "name": "mintedAt",       "type": "uint256"},
            {"internalType": "uint256", "name": "totalDecisions", "type": "uint256"},
            {"internalType": "uint256", "name": "totalExecutions","type": "uint256"},
            {"internalType": "uint256", "name": "totalAborts",    "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]


class ERC8004Logger:
    """
    Logs agent decisions to AgentIdentity.sol on a given chain.
    Gracefully degrades if web3 or contract unavailable.
    """

    def __init__(self, chain: str = "mantle"):
        self.chain        = chain
        self._w3         = None
        self._contract   = None
        self._account    = None
        self._chain_id   = None
        self._ready      = False
        self._explorer   = _CHAIN_EXPLORER.get(chain, "https://explorer.mantle.xyz")

        try:
            self._setup()
        except Exception as exc:
            log.warning("ERC8004Logger unavailable for chain=%s: %s", chain, exc)

    def _setup(self) -> None:
        from web3 import Web3
        from eth_account import Account

        rpc_env, rpc_default = _CHAIN_RPC_ENV.get(self.chain, _CHAIN_RPC_ENV["mantle"])
        contract_env         = _CHAIN_CONTRACT_ENV.get(self.chain, "AGENT_IDENTITY_CONTRACT_ADDRESS")

        rpc_url          = os.getenv(rpc_env, rpc_default)
        contract_address = os.getenv(contract_env, "") or os.getenv("AGENT_IDENTITY_CONTRACT_ADDRESS", "")
        private_key      = os.getenv("DEPLOYER_PRIVATE_KEY", "")

        if not contract_address or not private_key:
            log.warning(
                "%s or DEPLOYER_PRIVATE_KEY not set for chain=%s",
                contract_env, self.chain,
            )
            return

        try:
            from web3.middleware import ExtraDataToPOAMiddleware
        except ImportError:
            from web3.middleware import geth_poa_middleware as ExtraDataToPOAMiddleware

        self._w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
        try:
            self._w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        except Exception:
            pass

        self._account  = Account.from_key(private_key)
        self._contract = self._w3.eth.contract(
            address=Web3.to_checksum_address(contract_address),
            abi=_ABI,
        )
        self._chain_id = self._w3.eth.chain_id
        self._ready    = True
        log.info(
            "ERC8004Logger ready — contract=%s chain=%s",
            contract_address[:12], self._chain_id,
        )

    def log_decision(
        self,
        agent_id:    int,
        signal_id:   str,
        action_type: str,
        success:     bool,
        detail:      str,
    ) -> Optional[str]:
        """
        Write a decision to AgentIdentity.sol.
        Returns tx_hash on success, None on failure.
        detail = tx hash (success) or abort reason (failure).
        """
        if not self._ready:
            log.debug("ERC8004Logger not ready — decision not logged on-chain")
            return None

        try:
            sig_id_int = int(signal_id) if signal_id.isdigit() else 0
        except (ValueError, AttributeError):
            sig_id_int = 0

        try:
            fn = self._contract.functions.logDecision(
                agent_id, sig_id_int, action_type, success, detail
            )
            nonce     = self._w3.eth.get_transaction_count(self._account.address)
            gas_est   = fn.estimate_gas({"from": self._account.address})
            tx = fn.build_transaction({
                "from":     self._account.address,
                "nonce":    nonce,
                "gas":      int(gas_est * 1.2),
                "gasPrice": self._w3.eth.gas_price,
                "chainId":  self._chain_id,
            })
            signed  = self._w3.eth.account.sign_transaction(tx, self._account.key)
            tx_hash = self._w3.eth.send_raw_transaction(signed.rawTransaction)
            receipt = self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

            if receipt["status"] == 1:
                tx_hex = tx_hash.hex()
                log.info(
                    "🔐 Decision logged on-chain: agent=%d success=%s tx=%s",
                    agent_id, success, tx_hex[:14],
                )
                return tx_hex
            else:
                log.error("logDecision tx reverted: %s", tx_hash.hex())
                return None

        except Exception as exc:
            log.warning("ERC8004 log failed: %s", exc)
            return None

    def mint_agent(self, owner_wallet: str, name: str) -> Optional[int]:
        """
        Mint a new agent identity NFT. Returns agent_id on success.
        Only needed once per agent at registration time.
        """
        if not self._ready:
            return None
        try:
            from web3 import Web3
            fn    = self._contract.functions.mintAgent(
                Web3.to_checksum_address(owner_wallet), name
            )
            nonce = self._w3.eth.get_transaction_count(self._account.address)
            tx    = fn.build_transaction({
                "from":     self._account.address,
                "nonce":    nonce,
                "gas":      200_000,
                "gasPrice": self._w3.eth.gas_price,
                "chainId":  self._chain_id,
            })
            signed  = self._w3.eth.account.sign_transaction(tx, self._account.key)
            tx_hash = self._w3.eth.send_raw_transaction(signed.rawTransaction)
            receipt = self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            agent_id = int(receipt["logs"][0]["topics"][1].hex(), 16)
            log.info("Agent minted: id=%d owner=%s tx=%s", agent_id, owner_wallet[:12], tx_hash.hex()[:14])
            return agent_id
        except Exception as exc:
            log.warning("mintAgent failed: %s", exc)
            return None

    def decision_count(self, agent_id: int) -> int:
        """How many decisions has this agent logged?"""
        if not self._ready:
            return 0
        try:
            return self._contract.functions.decisionCount(agent_id).call()
        except Exception:
            return 0

    @property
    def explorer_url(self) -> str:
        contract_env = _CHAIN_CONTRACT_ENV.get(self.chain, "AGENT_IDENTITY_CONTRACT_ADDRESS")
        addr = os.getenv(contract_env, "") or os.getenv("AGENT_IDENTITY_CONTRACT_ADDRESS", "")
        return f"{self._explorer}/address/{addr}"
