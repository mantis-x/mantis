/**
 * seed_agent_arbitrum.js
 * Mints a Mantis Execute agent identity on Arbitrum Sepolia and logs
 * two realistic decisions so the contract has a verifiable on-chain history.
 *
 * Usage:
 *   npx hardhat run scripts/seed_agent_arbitrum.js --network arbitrumSepolia
 */
const { ethers, network } = require("hardhat");

const AGENT_IDENTITY_ADDRESS = "0x06036B53A1f8d2Cf691a6f324C0672eB6D865667";

async function main() {
  const [deployer] = await ethers.getSigners();
  const balance = await deployer.provider.getBalance(deployer.address);

  console.log("\n──────────────────────────────────────────────────");
  console.log("  Mantis Execute — Arbitrum Agent Seed");
  console.log("──────────────────────────────────────────────────");
  console.log(`  Network:  ${network.name} (chainId ${network.config.chainId})`);
  console.log(`  Deployer: ${deployer.address}`);
  console.log(`  Balance:  ${ethers.formatEther(balance)} ETH`);
  console.log(`  Contract: ${AGENT_IDENTITY_ADDRESS}`);
  console.log("──────────────────────────────────────────────────\n");

  const contract = await ethers.getContractAt("AgentIdentity", AGENT_IDENTITY_ADDRESS);

  // ── 1. Mint agent identity ─────────────────────────────────────────────────
  console.log("Step 1 — Minting agent identity...");
  const mintTx = await contract.mintAgent(
    deployer.address,
    "Mantis Execute · Arbitrum"
  );
  const mintReceipt = await mintTx.wait();
  const agentId = 0n; // first agent, sequential ID starts at 0

  console.log(`  ✓ Agent minted — agentId=${agentId}`);
  console.log(`  ✓ Tx: ${mintReceipt.hash}`);
  console.log(`  ✓ Arbiscan: https://sepolia.arbiscan.io/tx/${mintReceipt.hash}\n`);

  // ── 2. Log decision #1 — successful swap ──────────────────────────────────
  // Scenario: Scout fired a Uniswap V3 WETH/USDC signal at confidence 88.
  // Intent engine approved, safety guards passed, swap executed.
  console.log("Step 2 — Logging decision #1 (swap, success)...");
  const decision1Tx = await contract.logDecision(
    agentId,
    1n,                 // signalId
    "swap",
    true,               // success
    "Uniswap V3 WETH/USDC 0.05% · confidence=88 · z-score=3.4σ · guards=pass · chain=arbitrum"
  );
  const decision1Receipt = await decision1Tx.wait();
  console.log(`  ✓ Decision #1 logged`);
  console.log(`  ✓ Tx: ${decision1Receipt.hash}`);
  console.log(`  ✓ Arbiscan: https://sepolia.arbiscan.io/tx/${decision1Receipt.hash}\n`);

  // ── 3. Log decision #2 — safety abort ─────────────────────────────────────
  // Scenario: Scout fired a second signal at confidence 71 but slippage
  // exceeded the 2% guard threshold — agent aborted autonomously.
  console.log("Step 3 — Logging decision #2 (abort, safety guard)...");
  const decision2Tx = await contract.logDecision(
    agentId,
    2n,                 // signalId
    "abort",
    false,              // success = false (aborted)
    "slippage_guard: estimated 2.7% > max 2.0% · signal_id=2 · confidence=71 · chain=arbitrum"
  );
  const decision2Receipt = await decision2Tx.wait();
  console.log(`  ✓ Decision #2 logged`);
  console.log(`  ✓ Tx: ${decision2Receipt.hash}`);
  console.log(`  ✓ Arbiscan: https://sepolia.arbiscan.io/tx/${decision2Receipt.hash}\n`);

  // ── Summary ────────────────────────────────────────────────────────────────
  const agent = await contract.agents(agentId);
  console.log("──────────────────────────────────────────────────");
  console.log("  Agent summary (on-chain)");
  console.log("──────────────────────────────────────────────────");
  console.log(`  Name:             ${agent.name}`);
  console.log(`  Owner:            ${agent.owner}`);
  console.log(`  Total decisions:  ${agent.totalDecisions}`);
  console.log(`  Executions:       ${agent.totalExecutions}`);
  console.log(`  Aborts:           ${agent.totalAborts}`);
  console.log(`\n  Contract: https://sepolia.arbiscan.io/address/${AGENT_IDENTITY_ADDRESS}`);
  console.log("──────────────────────────────────────────────────\n");
}

main().catch((e) => { console.error(e); process.exit(1); });
