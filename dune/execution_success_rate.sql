-- Mantis · Execution success rate (all time and 7-day rolling)
-- Source: AgentIdentity.sol — DecisionLogged events
-- Event: DecisionLogged(uint256 agentId, bytes32 signalId, uint8 actionType, bool success, string detail)
-- Topic: 0x424f0dfca9674c91095ca1a8556a6807fe62c984ea3d0132da3fffefcd048806
--
-- actionType: 0=swap, 1=add_liquidity, 2=remove_liquidity, 3=abort
-- success flag: true = executed on-chain, false = guard-aborted

WITH decisions AS (
    SELECT
        block_time,
        -- actionType is the 3rd non-indexed param (byte offset 64 in data)
        CAST(BYTEARRAY_TO_BIGINT(SUBSTR(data, 65, 32)) AS INT)    AS action_type,
        -- success is the 4th param (byte offset 96)
        (BYTEARRAY_TO_BIGINT(SUBSTR(data, 97, 32)) > 0)           AS success
    FROM arbitrum.logs
    WHERE contract_address = LOWER('{{agent_identity_address}}')
      AND topic0 = '0x424f0dfca9674c91095ca1a8556a6807fe62c984ea3d0132da3fffefcd048806'
)

SELECT
    CASE action_type
        WHEN 0 THEN 'swap'
        WHEN 1 THEN 'add_liquidity'
        WHEN 2 THEN 'remove_liquidity'
        WHEN 3 THEN 'abort'
        ELSE 'unknown'
    END                                                          AS action,
    COUNT(*)                                                     AS total,
    SUM(CASE WHEN success THEN 1 ELSE 0 END)                     AS executed,
    SUM(CASE WHEN NOT success THEN 1 ELSE 0 END)                 AS aborted,
    ROUND(100.0 * SUM(CASE WHEN success THEN 1 ELSE 0 END)
          / NULLIF(COUNT(*), 0), 1)                              AS success_rate_pct
FROM decisions
GROUP BY action_type, action
ORDER BY total DESC
