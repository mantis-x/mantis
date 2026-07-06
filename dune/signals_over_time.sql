-- Mantis · Signals over time (daily)
-- Source: SignalAuditLog.sol on Arbitrum One
-- Event: SignalLogged(bytes32 signalHash, address reporter, uint256 timestamp)
-- Topic: 0x5ab1c8eb89ff3aeeaaddfb82b2ef1c382144552809971e63d68532307612c03d
--
-- Replace {{signal_audit_log_address}} with the mainnet contract address.

SELECT
    date_trunc('day', block_time)                          AS day,
    COUNT(*)                                               AS signals,
    SUM(COUNT(*)) OVER (ORDER BY date_trunc('day', block_time)) AS cumulative
FROM arbitrum.logs
WHERE contract_address = LOWER('{{signal_audit_log_address}}')
  AND topic0 = '0x5ab1c8eb89ff3aeeaaddfb82b2ef1c382144552809971e63d68532307612c03d'
  AND block_time >= NOW() - INTERVAL '30' DAY
GROUP BY 1
ORDER BY 1
