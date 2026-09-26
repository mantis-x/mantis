const { ethers, network } = require("hardhat");
const fs = require("fs");
const path = require("path");
const { waitForReceipt } = require("./wait_for_receipt");

async function main() {
  const [deployer] = await ethers.getSigners();
  console.log(`Deploying AgentIdentity on ${network.name}`);
  console.log(`Deployer: ${deployer.address}`);

  const Factory = await ethers.getContractFactory("AgentIdentity");
  const contract = await Factory.deploy();
  const deployTx = contract.deploymentTransaction();
  if (!deployTx) throw new Error("Deployment transaction was not created");
  const deployReceipt = await waitForReceipt(network.config.url, deployTx.hash);
  if (deployReceipt.status !== "0x1") {
    throw new Error(`Deployment transaction reverted: ${deployTx.hash}`);
  }

  const address = ethers.getCreateAddress({
    from: deployer.address,
    nonce: deployTx.nonce,
  });
  console.log(`AgentIdentity deployed: ${address}`);

  const receipt = {
    network: network.name,
    contract: "AgentIdentity",
    address,
    deployer: deployer.address,
    timestamp: new Date().toISOString(),
  };
  const outPath = path.join(__dirname, `../deployments/${network.name}/AgentIdentity.json`);
  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  fs.writeFileSync(outPath, JSON.stringify(receipt, null, 2));
  console.log(`Receipt saved: ${outPath}`);
  console.log(`\nAdd to .env:\nAGENT_IDENTITY_CONTRACT_ADDRESS=${address}`);
}

main().catch((e) => { console.error(e); process.exit(1); });
