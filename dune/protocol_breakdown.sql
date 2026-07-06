-- Mantis · Signal count by protocol (last 30 days)
-- Decodes the reporter address in SignalLogged to identify which protocol
-- collector submitted each signal. Falls back to grouping by reporter address
-- if no label is matched.
--
-- Protocol collector wallet addresses must be filled in below.

WITH signals AS (
    SELECT
        block_time,
        topic1                                           AS signal_hash_topic,
        CONCAT('0x', SUBSTR(CAST(topic2 AS VARCHAR), 27)) AS reporter
    FROM arbitrum.logs
    WHERE contract_address = LOWER('{{signal_audit_log_address}}')
      AND topic0 = '0x5ab1c8eb89ff3aeeaaddfb82b2ef1c382144552809971e63d68532307612c03d'
      AND block_time >= NOW() - INTERVAL '30' DAY
)

SELECT
    CASE reporter
        WHEN LOWER('{{agni_collector_wallet}}')     THEN 'agni_finance'
        WHEN LOWER('{{univ3_collector_wallet}}')    THEN 'uniswap_v3'
        WHEN LOWER('{{trader_joe_collector_wallet}}') THEN 'trader_joe'
        WHEN LOWER('{{gmx_collector_wallet}}')      THEN 'gmx'
        WHEN LOWER('{{merchant_moe_collector_wallet}}') THEN 'merchant_moe'
        ELSE reporter
    END                                                AS protocol,
    COUNT(*)                                           AS signals,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
FROM signals
GROUP BY 1
ORDER BY signals DESC
