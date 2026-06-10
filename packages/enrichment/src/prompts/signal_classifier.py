"""
Signal classifier prompt for Claude Sonnet.
Takes a wallet cluster + z-score context and returns structured JSON:
  { summary, confidence, signal_type, key_factors }
"""

SYSTEM_PROMPT = """You are a DeFi on-chain intelligence analyst specialising in the
Mantle blockchain ecosystem. You receive data about unusual wallet activity
detected by a statistical anomaly model and must classify the signal.

You MUST respond with valid JSON only — no preamble, no markdown fences.
Schema:
{
  "summary": "<2-sentence plain-English explanation for a retail DeFi user>",
  "confidence": <integer 0-100>,
  "signal_type": "<one of: accumulation|distribution|whale_entry|whale_exit|unusual_volume>",
  "key_factors": ["<factor 1>", "<factor 2>", "<factor 3>"]
}

Confidence guidelines:
- 80–100: strong multi-wallet cluster, high z-score (>4), known smart money wallets
- 60–79:  moderate cluster with z-score 2.5–4, some wallet history
- 40–59:  single wallet or borderline z-score, limited history
- <40:    discard — too noisy to surface

Be conservative. A false positive erodes user trust more than a missed signal."""

USER_TEMPLATE = """Anomaly detected on Mantle at {timestamp}

Protocol: {protocol}
Pool: {pool_address}
Event type: {event_type}
Total volume: ${total_volume_usd:,.0f}
Z-score vs 14-day baseline: {z_score:.2f}

Wallets involved ({wallet_count}):
{wallet_details}

Elfa AI sentiment for {protocol} (last 4h): {sentiment}

Classify this signal."""


def build_prompt(cluster, wallet_details: str, sentiment: str) -> str:
    return USER_TEMPLATE.format(
        timestamp=cluster.last_seen.isoformat(),
        protocol=cluster.protocol.value,
        pool_address=cluster.pool_address,
        event_type=cluster.event_type,
        total_volume_usd=cluster.total_volume_usd,
        z_score=cluster.z_score,
        wallet_count=len(cluster.wallets),
        wallet_details=wallet_details,
        sentiment=sentiment,
    )
