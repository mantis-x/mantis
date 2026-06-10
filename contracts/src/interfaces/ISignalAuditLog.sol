// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title  ISignalAuditLog
 * @notice Interface for contracts that need to read or write to the
 *         SignalAuditLog. Use this instead of importing the full contract
 *         to keep downstream contract sizes small.
 */
interface ISignalAuditLog {

    event SignalLogged(
        uint256 indexed signalId,
        bytes32 indexed signalHash,
        string          protocol,
        string          signalType,
        uint8           confidence,
        uint256         timestamp
    );

    function logSignal(
        bytes32     signalHash,
        string calldata protocol,
        string calldata signalType,
        uint8       confidenceScore
    ) external returns (uint256 signalId);

    function verify(
        uint256 signalId,
        string calldata payloadJSON
    ) external view returns (bool valid, uint256 storedAt);

    function verifyHash(
        uint256 signalId,
        bytes32 claimedHash
    ) external view returns (bool valid, uint256 storedAt);

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
        );

    function getRecentIds(uint256 offset, uint256 limit)
        external
        view
        returns (uint256[] memory ids);

    function totalSignals()       external view returns (uint256);
    function authorisedLogger()   external view returns (address);
    function signalsByProtocol(string calldata) external view returns (uint256);
    function signalsByType(string calldata)     external view returns (uint256);
    function rotateLogger(address newLogger)    external;
}
