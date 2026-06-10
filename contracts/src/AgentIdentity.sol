// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title  AgentIdentity
 * @notice ERC-8004 inspired on-chain identity for AlphaExecutor agents.
 *
 * Each agent registered in AlphaExecutor mints one identity token.
 * Every execution decision (success or abort) is logged here, forming
 * an auditable reputation record that grows over the agent's lifetime.
 *
 * Track 6 — Mantle Turing Test Hackathon 2026
 */
contract AgentIdentity {

    struct Agent {
        address owner;
        string  name;
        uint256 mintedAt;
        uint256 totalDecisions;
        uint256 totalExecutions;
        uint256 totalAborts;
    }

    struct DecisionLog {
        uint256 agentId;
        uint256 signalId;
        string  actionType;    // "swap" | "add_liquidity" | "abort"
        bool    success;
        string  detail;        // tx hash or abort reason
        uint256 timestamp;
    }

    mapping(uint256 => Agent)        public agents;
    mapping(uint256 => DecisionLog[]) public decisions;
    uint256 public agentCount;

    address public immutable owner;

    event AgentMinted(uint256 indexed agentId, address indexed agentOwner, string name);
    event DecisionLogged(
        uint256 indexed agentId,
        uint256 indexed signalId,
        string  actionType,
        bool    success,
        string  detail,
        uint256 timestamp
    );

    constructor() {
        owner = msg.sender;
    }

    /**
     * @notice Mint a new agent identity NFT.
     * @param agentOwner  Wallet that owns and controls this agent
     * @param name        Human-readable agent name
     * @return agentId    Sequential ID for this agent
     */
    function mintAgent(address agentOwner, string calldata name)
        external
        returns (uint256 agentId)
    {
        agentId = agentCount++;
        agents[agentId] = Agent({
            owner:           agentOwner,
            name:            name,
            mintedAt:        block.timestamp,
            totalDecisions:  0,
            totalExecutions: 0,
            totalAborts:     0
        });
        emit AgentMinted(agentId, agentOwner, name);
    }

    /**
     * @notice Log a decision made by an agent.
     * @param agentId    The agent's identity token ID
     * @param signalId   The MantleScan Alpha signal that triggered this
     * @param actionType "swap" | "add_liquidity" | "abort"
     * @param success    True if execution completed, false if aborted
     * @param detail     tx hash on success, abort reason on failure
     */
    function logDecision(
        uint256 agentId,
        uint256 signalId,
        string  calldata actionType,
        bool    success,
        string  calldata detail
    ) external {
        Agent storage a = agents[agentId];
        a.totalDecisions++;
        if (success) a.totalExecutions++;
        else         a.totalAborts++;

        decisions[agentId].push(DecisionLog({
            agentId:    agentId,
            signalId:   signalId,
            actionType: actionType,
            success:    success,
            detail:     detail,
            timestamp:  block.timestamp
        }));

        emit DecisionLogged(agentId, signalId, actionType, success, detail, block.timestamp);
    }

    /**
     * @notice Fetch a single decision log entry.
     */
    function getDecision(uint256 agentId, uint256 index)
        external
        view
        returns (DecisionLog memory)
    {
        return decisions[agentId][index];
    }

    /**
     * @notice How many decisions has this agent logged?
     */
    function decisionCount(uint256 agentId) external view returns (uint256) {
        return decisions[agentId].length;
    }
}
