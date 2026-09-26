/**
 * deploy_audit_log.js
 * Deploys SignalAuditLog to Mantle Sepolia or Mantle mainnet.
 *
 * Usage:
 *   npx hardhat run scripts/deploy_audit_log.js --network mantleSepolia
 *   npx hardhat run scripts/deploy_audit_log.js --network mantle
 *
 * Required env vars:
 *   DEPLOYER_PRIVATE_KEY   — deployer wallet key
 *   LOGGER_WALLET_ADDRESS  — delivery worker wallet (optional; falls back to deployer)
 */
const { ethers, network } = require("hardhat");
const fs   = require("fs");
const path = require("path");
const { waitForReceipt } = require("./wait_for_receipt");

async function main() {
  // ── Preflight ──────────────────────────────────────────────────────────
  const [deployer] = await ethers.getSigners();
  const balance    = await deployer.provider.getBalance(deployer.address);
  const loggerAddr = process.env.LOGGER_WALLET_ADDRESS || deployer.address;

  console.log("\n──────────────────────────────────────────");
  console.log("  Deploying SignalAuditLog");
  console.log("──────────────────────────────────────────");
  console.log(`  Network:   ${network.name} (chainId ${network.config.chainId})`);
  console.log(`  Deployer:  ${deployer.address}`);
  console.log(`  Balance:   ${ethers.formatEther(balance)} (native token)`);
  console.log(`  Logger:    ${loggerAddr}`);
  console.log("──────────────────────────────────────────\n");

  if (balance === 0n) {
    throw new Error(`Deployer wallet has 0 balance on ${network.name}. Fund it first.`);
  }

  // ── Deploy ─────────────────────────────────────────────────────────────
  console.log("Deploying…");
  const Factory  = await ethers.getContractFactory("SignalAuditLog");
  const contract = await Factory.deploy(loggerAddr);

  process.stdout.write("Waiting for confirmation");
  const deployTx = contract.deploymentTransaction();
  if (!deployTx) throw new Error("Deployment transaction was not created");
  const interval = setInterval(() => process.stdout.write("."), 1500);

  const deployReceipt = await waitForReceipt(network.config.url, deployTx.hash);
  if (deployReceipt.status !== "0x1") {
    throw new Error(`Deployment transaction reverted: ${deployTx.hash}`);
  }
  clearInterval(interval);
  console.log(" done.\n");

  const address = ethers.getCreateAddress({
    from: deployer.address,
    nonce: deployTx.nonce,
  });
  const deployed = Factory.attach(address);
  const txHash  = deployTx?.hash ?? "n/a";

  // ── Verify deployment ──────────────────────────────────────────────────
  const onChainLogger = await deployed.authorisedLogger();
  const onChainOwner  = await deployed.owner();

  console.log("  ✓ Contract deployed");
  console.log(`    Address:  ${address}`);
  console.log(`    Tx hash:  ${txHash}`);
  console.log(`    Owner:    ${onChainOwner}`);
  console.log(`    Logger:   ${onChainLogger}`);

  // Quick smoke test: log one signal and verify it
  console.log("\n  Running smoke test…");
  const testPayload = JSON.stringify({
    confidence: 99,
    deliver_at: new Date().toISOString(),
    id: 0,
    pool: deployer.address,
    protocol: "agni_finance",
    signal_type: "accumulation",
    summary: "Deployment smoke test signal.",
  });
  const testHash = ethers.keccak256(ethers.toUtf8Bytes(testPayload));

  // Only attempt if deployer == logger
  if (onChainLogger.toLowerCase() === deployer.address.toLowerCase()) {
    const logTx = await deployed.logSignal(testHash, "agni_finance", "accumulation", 99);
    await waitForReceipt(network.config.url, logTx.hash);
    const [valid] = await deployed.verify(0, testPayload);
    if (!valid) throw new Error("Smoke test FAILED: verify() returned false");
    console.log("  ✓ Smoke test passed (logSignal + verify)");
  } else {
    console.log("  ○ Skipping smoke test (deployer ≠ logger)");
  }

  // ── Save receipt ───────────────────────────────────────────────────────
  const receipt = {
    network:      network.name,
    chainId:      network.config.chainId,
    contract:     "SignalAuditLog",
    address,
    txHash,
    deployer:     deployer.address,
    logger:       onChainLogger,
    deployedAt:   new Date().toISOString(),
  };

  const outDir  = path.join(__dirname, `../deployments/${network.name}`);
  const outPath = path.join(outDir, "SignalAuditLog.json");
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(outPath, JSON.stringify(receipt, null, 2));

  // ── Instructions ───────────────────────────────────────────────────────
  // Was hardcoded to Mantle's two explorers only, so any other network
  // (arbitrum, hashkey, ethereum, ...) printed a wrong Mantle-Sepolia link
  // in this console output — cosmetic only, didn't affect the deployment
  // or the saved receipt.json. Generalized ahead of the Ethereum deploy.
  const EXPLORERS = {
    mantle:         "https://explorer.mantle.xyz",
    mantleSepolia:  "https://explorer.sepolia.mantle.xyz",
    arbitrum:       "https://arbiscan.io",
    arbitrumSepolia:"https://sepolia.arbiscan.io",
    hashkey:        "https://hsk.blockscout.com",
    hashkeyTestnet: "https://testnet.hsk.blockscout.com",
    ethereum:       "https://etherscan.io",
    ethereumSepolia:"https://sepolia.etherscan.io",
  };
  const explorerBase = EXPLORERS[network.name] || "https://explorer.mantle.xyz";

  console.log(`\n  ✓ Receipt saved: ${outPath}`);
  console.log(`\n  Explorer: ${explorerBase}/address/${address}`);
  console.log(`\n  ─── Add to your .env ───────────────────`);
  console.log(`  AUDIT_CONTRACT_ADDRESS=${address}`);
  console.log(`  ────────────────────────────────────────\n`);
}

main().catch((e) => {
  console.error("\n✗ Deployment failed:", e.message);
  process.exit(1);
});
