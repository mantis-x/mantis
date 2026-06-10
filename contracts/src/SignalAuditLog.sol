// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title  SignalAuditLog
 * @author MantleScan Alpha — Mantle Turing Test Hackathon 2026, Track 2
 *
 * @notice Immutable on-chain audit trail for every signal published by
 *         MantleScan Alpha. Provides cryptographic proof that a signal
 *         existed at a specific time and has not been altered since.
 *
 * @dev    Architecture
 *         ─────────────
 *         The delivery worker calls logSignal() immediately after
 *         dispatching a Telegram alert. The returned signalId is stored
 *         in the off-chain database alongside the signal record.
 *
 *         Anyone can call verify() with the original JSON payload to
 *         confirm it matches the stored hash — useful for judges,
 *         institutional clients, or on-chain consumers.
 *
 *         The contract is intentionally minimal:
 *           • No upgradability (immutability is the point)
 *           • No token gating (public log, public verify)
 *           • One authorised logger address per deployment
 *           • Owner can rotate the logger address if key is compromised
 *
 *         Signal hash = keccak256(abi.encodePacked(signalJSON))
 *         where signalJSON is the canonical UTF-8 JSON string:
 *           {
 *             "id": <int>,
 *             "protocol": "<string>",
 *             "pool": "<checksummed address>",
 *             "signal_type": "<string>",
 *             "confidence": <int>,
 *             "summary": "<string>",
 *             "deliver_at": "<ISO-8601 UTC>"
 *           }
 *         Key ordering MUST be stable (alphabetical) to ensure hash
 *         reproducibility across languages. See docs/smart_contracts.md.
 */
contract SignalAuditLog {

    // ─── Structs ────────────────────────────────────────────────────────────

    struct LogEntry {
        bytes32 signalHash;       // keccak256 of canonical signal JSON
        uint256 timestamp;        // block.timestamp at log time
        address logger;           // who submitted this entry
        uint8   confidenceScore;  // 0–100, copied from signal for quick reads
        string  protocol;         // "merchant_moe" | "agni_finance" | "fluxion"
        string  signalType;       // "accumulation" | "distribution" | ...
    }

    // ─── State ──────────────────────────────────────────────────────────────

    /// @notice Contract owner — can rotate the authorised logger
    address public immutable owner;

    /// @notice The only address permitted to call logSignal()
    address public authorisedLogger;

    /// @notice signalId → LogEntry
    mapping(uint256 => LogEntry) private _entries;

    /// @notice Total signals logged (= next signalId)
    uint256 public totalSignals;

    /// @notice Signals logged per protocol (for stats queries)
    mapping(string => uint256) public signalsByProtocol;

    /// @notice Signals logged per signal type
    mapping(string => uint256) public signalsByType;

    // ─── Events ─────────────────────────────────────────────────────────────

    event SignalLogged(
        uint256 indexed signalId,
        bytes32 indexed signalHash,
        string          protocol,
        string          signalType,
        uint8           confidence,
        uint256         timestamp
    );

    event LoggerRotated(
        address indexed previous,
        address indexed next
    );

    // ─── Errors ─────────────────────────────────────────────────────────────

    error OnlyOwner();
    error OnlyLogger();
    error ZeroAddress();
    error SignalNotFound(uint256 signalId);
    error InvalidConfidence(uint8 score);
    error EmptyHash();

    // ─── Modifiers ──────────────────────────────────────────────────────────

    modifier onlyOwner() {
        if (msg.sender != owner) revert OnlyOwner();
        _;
    }

    modifier onlyLogger() {
        if (msg.sender != authorisedLogger) revert OnlyLogger();
        _;
    }

    // ─── Constructor ────────────────────────────────────────────────────────

    /**
     * @param _logger  The delivery worker's wallet address.
     *                 Pass address(0) to use the deployer as logger.
     */
    constructor(address _logger) {
        owner = msg.sender;
        authorisedLogger = (_logger == address(0)) ? msg.sender : _logger;
    }

    // ─── Core: write ────────────────────────────────────────────────────────

    /**
     * @notice Record a signal hash on-chain immediately after dispatch.
     *
     * @param signalHash      keccak256(abi.encodePacked(canonicalJSON))
     * @param protocol        Protocol name string (e.g. "agni_finance")
     * @param signalType      Signal classification (e.g. "accumulation")
     * @param confidenceScore LLM confidence 0–100
     *
     * @return signalId       Sequentially assigned, returned for DB storage
     */
    function logSignal(
        bytes32     signalHash,
        string calldata protocol,
        string calldata signalType,
        uint8       confidenceScore
    )
        external
        onlyLogger
        returns (uint256 signalId)
    {
        if (signalHash == bytes32(0))          revert EmptyHash();
        if (confidenceScore > 100)             revert InvalidConfidence(confidenceScore);

        signalId = totalSignals;
        unchecked { totalSignals++; }

        _entries[signalId] = LogEntry({
            signalHash:      signalHash,
            timestamp:       block.timestamp,
            logger:          msg.sender,
            confidenceScore: confidenceScore,
            protocol:        protocol,
            signalType:      signalType
        });

        unchecked {
            signalsByProtocol[protocol]++;
            signalsByType[signalType]++;
        }

        emit SignalLogged(
            signalId,
            signalHash,
            protocol,
            signalType,
            confidenceScore,
            block.timestamp
        );
    }

    // ─── Core: read ─────────────────────────────────────────────────────────

    /**
     * @notice Verify a signal payload against its stored hash.
     *
     * @param signalId    The ID returned by logSignal()
     * @param payloadJSON The original canonical JSON string
     *
     * @return valid      True if hash matches — signal is authentic
     * @return storedAt   Block timestamp when it was logged
     */
    function verify(uint256 signalId, string calldata payloadJSON)
        external
        view
        returns (bool valid, uint256 storedAt)
    {
        if (signalId >= totalSignals) revert SignalNotFound(signalId);

        LogEntry storage e = _entries[signalId];
        bytes32 computed   = keccak256(abi.encodePacked(payloadJSON));
        valid    = (e.signalHash == computed);
        storedAt = e.timestamp;
    }

    /**
     * @notice Verify using a pre-computed hash (cheaper for callers who
     *         already have the hash, e.g. other contracts).
     */
    function verifyHash(uint256 signalId, bytes32 claimedHash)
        external
        view
        returns (bool valid, uint256 storedAt)
    {
        if (signalId >= totalSignals) revert SignalNotFound(signalId);

        LogEntry storage e = _entries[signalId];
        valid    = (e.signalHash == claimedHash);
        storedAt = e.timestamp;
    }

    /**
     * @notice Fetch all fields for a log entry.
     */
    function getEntry(uint256 signalId)
        external
        view
        returns (
            bytes32 signalHash,
            uint256 timestamp,
            address logger,
            uint8   confidence,
            string memory protocol,
            string memory signalType
        )
    {
        if (signalId >= totalSignals) revert SignalNotFound(signalId);

        LogEntry storage e = _entries[signalId];
        return (
            e.signalHash,
            e.timestamp,
            e.logger,
            e.confidenceScore,
            e.protocol,
            e.signalType
        );
    }

    /**
     * @notice Fetch a page of signalIds in reverse order (newest first).
     *         Useful for building an audit feed UI.
     *
     * @param offset  Start index from newest (0 = most recent)
     * @param limit   Max entries to return (capped at 100)
     */
    function getRecentIds(uint256 offset, uint256 limit)
        external
        view
        returns (uint256[] memory ids)
    {
        if (totalSignals == 0) return ids;
        if (limit > 100) limit = 100;

        uint256 newest = totalSignals - 1;
        if (offset > newest) return ids;

        uint256 start = newest - offset;
        uint256 count = (start + 1 < limit) ? start + 1 : limit;
        ids = new uint256[](count);

        for (uint256 i = 0; i < count; ) {
            ids[i] = start - i;
            unchecked { i++; }
        }
    }

    // ─── Admin ──────────────────────────────────────────────────────────────

    /**
     * @notice Rotate the authorised logger (e.g. after a key rotation).
     *         Does not affect historical entries — they record logger at log time.
     */
    function rotateLogger(address newLogger) external onlyOwner {
        if (newLogger == address(0)) revert ZeroAddress();
        emit LoggerRotated(authorisedLogger, newLogger);
        authorisedLogger = newLogger;
    }
}
