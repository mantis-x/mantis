-- Mantis · Signal count by chain (last 30 days)
-- Unions Arbitrum and Mantle signal logs into a single chain-breakdown view.
-- Mantle is queried via the evm.logs community dataset (Dune Spellbook).
-- Note: replace both contract addresses with mainnet deployments.

WITH arb_signals AS (
    SELECT
        'arbitrum'               AS chain,
        DATE(block_time)         AS day,
        COUNT(*)                 AS cnt
    FROM arbitrum.logs
    WHERE contract_address = LOWER('{{arb_signal_audit_log}}')
      AND topic0 = '0x5ab1c8eb89ff3aeeaaddfb82b2ef1c382144552809971e63d68532307612c03d'
      AND block_time >= NOW() - INTERVAL '30' DAY
    GROUP BY 1, 2
),

mantle_signals AS (
    SELECT
        'mantle'                 AS chain,
        DATE(block_time)         AS day,
        COUNT(*)                 AS cnt
    FROM evm.logs
    WHERE blockchain = 'mantle'
      AND contract_address = LOWER('{{mantle_signal_audit_log}}')
      AND topic0 = '0x5ab1c8eb89ff3aeeaaddfb82b2ef1c382144552809971e63d68532307612c03d'
      AND block_time >= NOW() - INTERVAL '30' DAY
    GROUP BY 1, 2
)

SELECT
    chain,
    SUM(cnt)                                        AS total_signals,
    ROUND(100.0 * SUM(cnt) / SUM(SUM(cnt)) OVER (), 1) AS pct
FROM (SELECT * FROM arb_signals UNION ALL SELECT * FROM mantle_signals)
GROUP BY chain
ORDER BY total_signals DESC
