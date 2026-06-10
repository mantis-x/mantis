const { ethers, network } = require("hardhat");
const fs = require("fs");
const path = require("path");

async function main() {
  const [deployer] = await ethers.getSigners();
  console.log(`Deploying AgentIdentity on ${network.name}`);
  console.log(`Deployer: ${deployer.address}`);

  const Factory = await ethers.getContractFactory("AgentIdentity");
  const contract = await Factory.deploy();
  await contract.waitForDeployment();

  const address = await contract.getAddress();
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
