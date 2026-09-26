# Curvegrid — Best AI Agent Project

## Summary

Mantis is an AI agent that detects unusual on-chain activity, proposes a
trade, requires verified human consent for consequential actions, executes
approved Arbitrum swaps through Uniswap V3, and records both executions and
aborts through AgentIdentity.

## MultiBaaS usage

Mantis does not currently use MultiBaaS. Its chain access uses direct RPC
providers and web3.py; its on-chain reputation record is the existing
AgentIdentity contract.

## Team and socials

Project repository: https://github.com/mantis-x/mantis

Add the individual team names and social links from the ETHGlobal team profile
before submission; they are intentionally not guessed here.

## Setup and testing

```bash
cp .env.example .env
pip install -r requirements.txt
python3 -m compileall -q packages/executor/src
```

The executor defaults to dry-run mode. Configure the World sandbox variables
in `.env` before demonstrating a protected trade. Set
`WORLD_ID_APPROVAL_THRESHOLD_USD=0` to gate every action. The approval tests
are in `packages/executor/tests/test_world_id_approval.py` and the executor
boundary tests are in `packages/executor/tests/test_executor_world_gate.py`.

## Feedback

The strongest part of the architecture is the shared execution boundary:
World approval, safety guards, Uniswap routing, and AgentIdentity logging are
composed around one `ExecutionRequest`. The remaining integration friction is
obtaining the event sandbox credentials and running a live proof/trade demo;
those are documented in `docs/world_id_integration.md`.
