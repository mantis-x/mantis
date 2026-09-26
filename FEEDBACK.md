# Mantis sponsor feedback

## Uniswap Foundation

Mantis Execute routes Arbitrum swaps through Uniswap V3 `SwapRouter02`. The
integration is in [`packages/executor/src/arbitrum/swap_executor.py`](packages/executor/src/arbitrum/swap_executor.py):
it obtains a fresh QuoterV2 quote, derives `amountOutMinimum` from the user
slippage limit, and only then sends `exactInputSingle`. Dry-run mode returns a
simulated result without moving funds.

The most useful developer experience detail was being able to keep the venue
behind the existing `ExecutionRequest`/`ExecutionResult` boundary: the same
guards, daily caps, kill switch, and ERC-8004 decision log work across Mantle
and Arbitrum. The remaining production work is operational—key custody,
allowance policy, and a paid RPC—not a second execution model.

## World ID for Agents

World is used as a human-consent boundary, not as a decorative login. A
qualifying trade is blocked until the backend validates a proof against the
configured World endpoint. A rejected, expired, missing, or unverifiable proof
never reaches Uniswap and is recorded as an aborted AgentIdentity decision.

## Curvegrid

Mantis is an AI agent project spanning detection, decisioning, human approval,
execution, and on-chain reputation. See the README and
`packages/executor/tests/test_world_id_approval.py` for the end-to-end policy
boundary and failure-path coverage.
