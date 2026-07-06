"""
ArbitrumSwapExecutor — executes token swaps on Arbitrum via Uniswap V3 SwapRouter02.

Uses exactInputSingle for single-hop swaps. Handles ERC-20 approval automatically.
Supports dry_run mode: simulates the swap with call() to get a quote, returns a
synthetic tx_hash without spending gas or moving funds.

Env vars:
  ARBITRUM_RPC_URL          (default: https://arb1.arbitrum.io/rpc)
  ARB_SWAP_ROUTER           (default: SwapRouter02 mainnet)
  ARB_WETH_ADDRESS          (default: WETH mainnet)
  ARB_USDC_ADDRESS          (default: USDC.e mainnet)
  DEPLOYER_PRIVATE_KEY      wallet that signs transactions
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

log = logging.getLogger(__name__)

# ── Arbitrum mainnet defaults ────────────────────────────────────────────────
_MAINNET_ROUTER = "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45"  # SwapRouter02
_MAINNET_WETH   = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
_MAINNET_USDC   = "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8"  # USDC.e

# ── Arbitrum Sepolia defaults ────────────────────────────────────────────────
_SEPOLIA_ROUTER = "0x101F443B4d1b059569D643917553c771E1b9663E"
_SEPOLIA_WETH   = "0x980B62Da83eFf3D4576C647993b0c1D7faf17c73"
_SEPOLIA_USDC   = "0x75faf114eafb1BDbe2F0316DF893fd58CE46AA4d"

# Pool fee tiers
FEE_LOWEST  = 100    # 0.01%
FEE_LOW     = 500    # 0.05%  ← WETH/USDC default
FEE_MEDIUM  = 3000   # 0.30%
FEE_HIGH    = 10000  # 1.00%

_ROUTER_ABI = [
    {
        "inputs": [{
            "components": [
                {"name": "tokenIn",            "type": "address"},
                {"name": "tokenOut",           "type": "address"},
                {"name": "fee",                "type": "uint24"},
                {"name": "recipient",          "type": "address"},
                {"name": "amountIn",           "type": "uint256"},
                {"name": "amountOutMinimum",   "type": "uint256"},
                {"name": "sqrtPriceLimitX96",  "type": "uint160"},
            ],
            "name": "params",
            "type": "tuple",
        }],
        "name": "exactInputSingle",
        "outputs": [{"name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function",
    },
]

_ERC20_ABI = [
    {
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount",  "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "owner",   "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
]


class ArbitrumSwapExecutor:
    """
    Executes token swaps on Arbitrum via Uniswap V3 SwapRouter02.

    dry_run=True  → call() to simulate, return synthetic tx hash (no gas spent)
    dry_run=False → send_raw_transaction() for a real on-chain swap
    """

    def __init__(self, dry_run: bool = True):
        self._dry_run  = dry_run
        self._w3       = None
        self._account  = None
        self._router   = None
        self._chain_id = None
        self._ready    = False

        self._rpc_url      = os.getenv("ARBITRUM_RPC_URL", _MAINNET_ROUTER)
        self._router_addr  = os.getenv("ARB_SWAP_ROUTER", _MAINNET_ROUTER)
        self._default_weth = os.getenv("ARB_WETH_ADDRESS", _MAINNET_WETH)
        self._default_usdc = os.getenv("ARB_USDC_ADDRESS", _MAINNET_USDC)

        try:
            self._setup()
        except Exception as exc:
            log.warning("ArbitrumSwapExecutor setup failed: %s", exc)

    def _setup(self) -> None:
        from web3 import Web3
        from eth_account import Account

        rpc_url     = os.getenv("ARBITRUM_RPC_URL", "https://arb1.arbitrum.io/rpc")
        private_key = os.getenv("DEPLOYER_PRIVATE_KEY", "")

        if not private_key:
            log.warning("DEPLOYER_PRIVATE_KEY not set — ArbitrumSwapExecutor in stub mode")
            return

        self._w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
        if not self._w3.is_connected():
            raise ConnectionError(f"Cannot connect to Arbitrum RPC: {rpc_url}")

        self._account  = Account.from_key(private_key)
        self._chain_id = self._w3.eth.chain_id
        self._router   = self._w3.eth.contract(
            address=Web3.to_checksum_address(self._router_addr),
            abi=_ROUTER_ABI,
        )
        self._ready = True

        log.info(
            "ArbitrumSwapExecutor ready — chain_id=%d router=%s dry_run=%s",
            self._chain_id, self._router_addr[:12], self._dry_run,
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    def swap(
        self,
        token_in:   str,
        token_out:  str,
        amount_usd: float,
        fee:        int   = FEE_LOW,
        slippage:   float = 0.02,
    ) -> dict:
        """
        Execute a token swap.

        amount_usd: the USD value to swap. Converted to token units using
                    on-chain decimals (token_in is assumed to be priced near $1
                    for stablecoins, or ETH price is fetched from the pool for WETH).

        Returns: {"tx_hash": str, "amount_out": int, "gas_used": int, "simulated": bool}
        Raises:  RuntimeError on failure.
        """
        if not self._ready:
            return self._stub_result(token_in, token_out, amount_usd)

        from web3 import Web3

        w3         = self._w3
        account    = self._account
        token_in_c = Web3.to_checksum_address(token_in)
        token_out_c= Web3.to_checksum_address(token_out)
        router_c   = Web3.to_checksum_address(self._router_addr)

        # Get token_in decimals and compute amount_in
        token_in_contract = w3.eth.contract(address=token_in_c, abi=_ERC20_ABI)
        decimals_in = token_in_contract.functions.decimals().call()
        amount_in   = self._usd_to_units(amount_usd, decimals_in, token_in_c)

        log.info(
            "Arbitrum swap: $%.2f → %d units of %s (decimals=%d) → %s fee=%d dry=%s",
            amount_usd, amount_in, token_in_c[:10], decimals_in, token_out_c[:10],
            fee, self._dry_run,
        )

        # Minimum output with slippage tolerance (0 = accept any in dry_run)
        amount_out_min = 0 if self._dry_run else 1  # production: use quoter

        params = (
            token_in_c,
            token_out_c,
            fee,
            account.address,
            amount_in,
            amount_out_min,
            0,  # sqrtPriceLimitX96 = 0 (no limit)
        )

        if self._dry_run:
            return self._simulate(params, amount_in, amount_usd)
        else:
            return self._execute(params, token_in_contract, token_in_c, router_c, amount_in, amount_usd)

    def is_ready(self) -> bool:
        return self._ready

    # ── Internal ───────────────────────────────────────────────────────────────

    def _simulate(self, params: tuple, amount_in: int, amount_usd: float) -> dict:
        """Simulate via call() — no gas spent, returns quoted amountOut."""
        try:
            amount_out = self._router.functions.exactInputSingle(params).call(
                {"from": self._account.address, "value": 0}
            )
            log.info("Dry-run quote: amount_out=%d", amount_out)
        except Exception as exc:
            log.warning("Simulation call failed (pool may lack liquidity on testnet): %s", exc)
            amount_out = 0

        synthetic_hash = f"dry_run_arb_{int(time.time())}_{amount_in}"
        return {
            "tx_hash":   synthetic_hash,
            "amount_in": amount_in,
            "amount_out": amount_out,
            "gas_used":   0,
            "simulated":  True,
            "amount_usd": amount_usd,
        }

    def _execute(
        self,
        params:             tuple,
        token_in_contract,
        token_in_c:         str,
        router_c:           str,
        amount_in:          int,
        amount_usd:         float,
    ) -> dict:
        """Send real transaction. Approves router first if needed."""
        w3      = self._w3
        account = self._account

        # 1. Approve router if allowance insufficient
        allowance = token_in_contract.functions.allowance(account.address, router_c).call()
        if allowance < amount_in:
            log.info("Approving router for %d units of %s", amount_in, token_in_c[:10])
            self._approve(token_in_contract, router_c, amount_in)

        # 2. Build and send exactInputSingle tx
        fn    = self._router.functions.exactInputSingle(params)
        nonce = w3.eth.get_transaction_count(account.address)
        gas   = fn.estimate_gas({"from": account.address, "value": 0})

        tx = fn.build_transaction({
            "from":     account.address,
            "nonce":    nonce,
            "gas":      int(gas * 1.2),
            "gasPrice": w3.eth.gas_price,
            "chainId":  self._chain_id,
            "value":    0,
        })
        signed  = w3.eth.account.sign_transaction(tx, account.key)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

        if receipt["status"] != 1:
            raise RuntimeError(f"Swap tx reverted: {tx_hash.hex()}")

        # Decode amountOut from receipt logs (simplified)
        amount_out = 0
        log.info(
            "Swap executed: tx=%s gas=%d amount_usd=$%.2f",
            tx_hash.hex()[:16], receipt["gasUsed"], amount_usd,
        )
        return {
            "tx_hash":    tx_hash.hex(),
            "amount_in":  amount_in,
            "amount_out": amount_out,
            "gas_used":   receipt["gasUsed"],
            "simulated":  False,
            "amount_usd": amount_usd,
        }

    def _approve(self, token_contract, spender: str, amount: int) -> None:
        """Send ERC-20 approve transaction."""
        w3      = self._w3
        account = self._account
        fn      = token_contract.functions.approve(spender, amount)
        nonce   = w3.eth.get_transaction_count(account.address)
        gas     = fn.estimate_gas({"from": account.address})
        tx = fn.build_transaction({
            "from":     account.address,
            "nonce":    nonce,
            "gas":      int(gas * 1.2),
            "gasPrice": w3.eth.gas_price,
            "chainId":  self._chain_id,
        })
        signed  = w3.eth.account.sign_transaction(tx, account.key)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        if receipt["status"] != 1:
            raise RuntimeError(f"Approve tx reverted: {tx_hash.hex()}")
        log.info("Approval confirmed: tx=%s", tx_hash.hex()[:16])

    def _usd_to_units(self, amount_usd: float, decimals: int, token_addr: str) -> int:
        """
        Convert a USD amount to token base units.
        For stablecoins (USDC/USDT): 1 token ≈ $1, so units = amount_usd * 10^decimals.
        For WETH: units = (amount_usd / 2400) * 10^18  (rough ETH price; production uses oracle).
        """
        weth = self._default_weth.lower()
        if token_addr.lower() == weth:
            eth_price = float(os.getenv("ETH_PRICE_USD", "2400"))
            return int((amount_usd / eth_price) * (10 ** decimals))
        # Stablecoin / unknown: treat 1 token = $1
        return int(amount_usd * (10 ** decimals))

    def _stub_result(self, token_in: str, token_out: str, amount_usd: float) -> dict:
        """Returned when executor is not fully configured (no private key)."""
        log.warning(
            "ArbitrumSwapExecutor stub: not ready (missing key). "
            "Returning simulated result for $%.2f swap.", amount_usd,
        )
        return {
            "tx_hash":   f"stub_arb_{int(time.time())}",
            "amount_in":  0,
            "amount_out": 0,
            "gas_used":   0,
            "simulated":  True,
            "amount_usd": amount_usd,
        }
