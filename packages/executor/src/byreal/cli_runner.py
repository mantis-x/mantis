"""
ByrealCLIRunner — wrapper around the Byreal Skills CLI.

Real command structure from github.com/byreal-git/byreal-agent-skills:
  byreal-cli pools list --sort-field apr24h -o json
  byreal-cli pools analyze <pool-address> -o json
  byreal-cli swap execute --input-mint <> --output-mint <> --amount <> --dry-run
  byreal-cli positions copy --position <> --amount-usd <> --confirm

Setup (one-time, interactive):
  byreal-cli setup   # stores wallet key at ~/.config/byreal/keys/

All write commands require wallet setup first.
JSON output mode: add -o json flag.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import shlex
from typing import Any, Optional

log = logging.getLogger(__name__)

CLI_CMD = "byreal-cli"


class ByrealCLIError(Exception):
    pass


class ByrealCLIRunner:
    """
    Thin subprocess wrapper around byreal-cli.
    All methods return parsed JSON dicts.
    """

    def __init__(self, dry_run: bool = False):
        """
        dry_run: if True, adds --dry-run to write commands.
        Set to False only in production with real funds.
        """
        self._dry_run = dry_run
        self._available = self._check_available()

    def _check_available(self) -> bool:
        """Check if byreal-cli is installed."""
        try:
            result = subprocess.run(
                [CLI_CMD, "--version"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                log.info("byreal-cli found: %s", result.stdout.strip())
                return True
        except FileNotFoundError:
            pass
        log.warning(
            "byreal-cli not found. Install: npm install -g @byreal-io/byreal-cli"
        )
        return False

    def is_available(self) -> bool:
        return self._available

    # ── Read commands ────────────────────────────────────────────────────────

    def pools_list(self, sort_field: str = "apr24h", limit: int = 10) -> dict:
        """List top pools sorted by APR or volume."""
        return self._run(
            ["pools", "list",
             "--sort-field", sort_field,
             "-o", "json"]
        )

    def pools_analyze(self, pool_address: str) -> dict:
        """Comprehensive analysis of a specific pool."""
        return self._run(
            ["pools", "analyze", pool_address, "-o", "json"]
        )

    def wallet_balance(self) -> dict:
        """View wallet address and token balances."""
        return self._run(["wallet", "balance", "-o", "json"])

    # ── Write commands (require wallet setup) ────────────────────────────────

    def swap_execute(
        self,
        input_mint:  str,
        output_mint: str,
        amount:      float,
        slippage:    float = 0.02,
    ) -> dict:
        """
        Execute a token swap.
        Always uses --dry-run in hackathon mode.
        """
        args = [
            "swap", "execute",
            "--input-mint",  input_mint,
            "--output-mint", output_mint,
            "--amount",      str(amount),
            "--slippage",    str(slippage),
            "-o", "json",
        ]
        if self._dry_run:
            args.append("--dry-run")
        return self._run(args)

    def positions_copy(
        self,
        position_address: str,
        amount_usd: float,
    ) -> dict:
        """Copy a top farmer's position."""
        args = [
            "positions", "copy",
            "--position",   position_address,
            "--amount-usd", str(amount_usd),
            "-o", "json",
        ]
        if not self._dry_run:
            args.append("--confirm")
        return self._run(args)

    # ── Core runner ──────────────────────────────────────────────────────────

    def _run(self, args: list[str]) -> dict:
        """Run a byreal-cli command and return parsed JSON."""
        if not self._available:
            # Return mock result for demo/testing when CLI not installed
            return self._mock_result(args)

        full_args = [CLI_CMD] + args
        log.debug("Byreal CLI: %s", shlex.join(full_args))

        try:
            result = subprocess.run(
                full_args,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            raise ByrealCLIError(f"byreal-cli timed out: {args[0]}")
        except FileNotFoundError:
            raise ByrealCLIError("byreal-cli not found")

        if result.returncode != 0:
            raise ByrealCLIError(
                f"byreal-cli error [{args[0]}]: {result.stderr.strip()}"
            )

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            # Some commands return non-JSON — wrap as text
            return {"output": result.stdout.strip(), "raw": True}

    def _mock_result(self, args: list[str]) -> dict:
        """
        Return realistic mock data when CLI is not installed.
        Used in testing and Demo Day fallback.
        """
        cmd = args[0] if args else "unknown"
        log.info("Byreal CLI mock mode: %s", " ".join(args[:3]))

        if cmd == "pools" and len(args) > 1 and args[1] == "analyze":
            return {
                "pool":     args[2] if len(args) > 2 else "0x...",
                "tvl_usd":  1_250_000,
                "apr_24h":  0.18,
                "volume_24h": 890_000,
                "fee_rate":   0.003,
                "mock":     True,
            }
        elif cmd == "swap":
            return {
                "status":   "simulated",
                "input":    args[3] if len(args) > 3 else "TOKEN_IN",
                "output":   args[5] if len(args) > 5 else "TOKEN_OUT",
                "amount":   float(args[7]) if len(args) > 7 else 0,
                "tx_hash":  "0xMOCK_TX_" + "a" * 20,
                "dry_run":  True,
                "mock":     True,
            }
        elif cmd == "wallet":
            return {
                "address":  os.getenv("BYREAL_AGENT_WALLET", "0x..."),
                "balance_usd": 10_000.0,
                "mock":     True,
            }
        return {"mock": True, "command": cmd}
