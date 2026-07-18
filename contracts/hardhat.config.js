require("@nomicfoundation/hardhat-toolbox");
require("dotenv").config();

module.exports = {
  solidity: {
    version: "0.8.20",
    settings: {
      optimizer: { enabled: true, runs: 200 },
    },
  },
  paths: {
    sources: "./src",
  },
  networks: {
    mantleSepolia: {
      url: process.env.MANTLE_SEPOLIA_RPC_URL || "https://rpc.sepolia.mantle.xyz",
      chainId: 5003,
      accounts: process.env.DEPLOYER_PRIVATE_KEY
        ? [process.env.DEPLOYER_PRIVATE_KEY]
        : [],
    },
    mantle: {
      url: process.env.MANTLE_RPC_URL || "https://rpc.mantle.xyz",
      chainId: 5000,
      accounts: process.env.DEPLOYER_PRIVATE_KEY
        ? [process.env.DEPLOYER_PRIVATE_KEY]
        : [],
    },
    // Arbitrum — Phase 1 (contracts not yet deployed here; entry enables future deployment)
    arbitrumSepolia: {
      url: process.env.ARBITRUM_SEPOLIA_RPC_URL || "https://sepolia-rollup.arbitrum.io/rpc",
      chainId: 421614,
      accounts: process.env.DEPLOYER_PRIVATE_KEY
        ? [process.env.DEPLOYER_PRIVATE_KEY]
        : [],
    },
    arbitrum: {
      url: process.env.ARBITRUM_RPC_URL || "https://arb1.arbitrum.io/rpc",
      chainId: 42161,
      accounts: process.env.DEPLOYER_PRIVATE_KEY
        ? [process.env.DEPLOYER_PRIVATE_KEY]
        : [],
    },
    // HashKey Chain — OP-stack L2, chain_id/RPC verified live 2026-07-09.
    // Testnet re-verified live 2026-07-11 (doc-provided
    // hashkeychain-testnet.alt.technology still doesn't resolve — that
    // testnet was retired; testnet.hsk.xyz is the current replacement).
    hashkeyTestnet: {
      url: process.env.HASHKEY_TESTNET_RPC_URL || "https://testnet.hsk.xyz",
      chainId: 133,
      accounts: process.env.DEPLOYER_PRIVATE_KEY
        ? [process.env.DEPLOYER_PRIVATE_KEY]
        : [],
    },
    hashkey: {
      url: process.env.HASHKEY_RPC_URL || "https://mainnet.hsk.xyz",
      chainId: 177,
      accounts: process.env.DEPLOYER_PRIVATE_KEY
        ? [process.env.DEPLOYER_PRIVATE_KEY]
        : [],
    },
    // Ethereum — added 2026-07-18. Ingestion watches mainnet (real Uniswap
    // V3 volume — see packages/ingestion/src/chains.py), but
    // SignalAuditLog/AgentIdentity are staged on Sepolia first, same as
    // Mantle/Arbitrum originally were, until the deployer wallet is funded
    // for a mainnet deploy. RPC verified live via eth_chainId before adding.
    ethereumSepolia: {
      url: process.env.ETHEREUM_SEPOLIA_RPC_URL || "https://ethereum-sepolia-rpc.publicnode.com",
      chainId: 11155111,
      accounts: process.env.DEPLOYER_PRIVATE_KEY
        ? [process.env.DEPLOYER_PRIVATE_KEY]
        : [],
    },
    ethereum: {
      url: process.env.ETHEREUM_RPC_URL || "https://ethereum.publicnode.com",
      chainId: 1,
      accounts: process.env.DEPLOYER_PRIVATE_KEY
        ? [process.env.DEPLOYER_PRIVATE_KEY]
        : [],
    },
  },
};
