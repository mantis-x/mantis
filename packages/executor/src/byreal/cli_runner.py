"""
ByrealCLIRunner: thin wrapper around the Byreal Skills CLI.
Calls the CLI via subprocess and returns structured output.

All Byreal interactions go through this module so they can be
swapped for a direct SDK call if the CLI changes.

Usage:
    runner = ByrealCLIRunner()
    result = runner.run("pool-query", {"network": "mantle", "pool": "0x..."})
"""
from __future__ import annotations
import json
import logging
import subprocess
import shlex
from typing import Any

log = logging.getLogger(__name__)

CLI_CMD = "byreal-cli"   # assumes `npm install -g @byreal-io/byreal-cli`


class ByrealCLIError(Exception):
    pass


class ByrealCLIRunner:
    def run(self, skill: str, params: dict[str, Any]) -> dict:
        """
        Run a Byreal skill and return parsed JSON output.
        Raises ByrealCLIError on non-zero exit or parse failure.
        """
        args = [CLI_CMD, skill]
        for key, val in params.items():
            args += [f"--{key}", str(val)]

        log.debug("Byreal CLI: %s", shlex.join(args))

        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            raise ByrealCLIError(f"Byreal CLI timed out: {skill}")
        except FileNotFoundError:
            raise ByrealCLIError(
                "byreal-cli not found. Run: npm install -g @byreal-io/byreal-cli"
            )

        if result.returncode != 0:
            raise ByrealCLIError(
                f"Byreal CLI error [{skill}]: {result.stderr.strip()}"
            )

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ByrealCLIError(
                f"Byreal CLI returned non-JSON output: {result.stdout[:200]}"
            ) from exc
