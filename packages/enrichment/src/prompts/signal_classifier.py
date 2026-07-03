"""
Claude Sonnet prompt for signal classification.

Given an anomaly candidate (wallet cluster + z-score context),
Claude returns structured JSON:
  { summary, confidence, signal_type, key_factors }

Key design decisions:
- System prompt establishes the DeFi analyst persona
- User prompt provides ALL context in a structured format
- Confidence guidelines calibrated for Mantle ecosystem
- JSON-only output enforced in system prompt
"""

SYSTEM_PROMPT = """You are a senior DeFi on-chain intelligence analyst specialising in EVM blockchain ecosystems. You have deep knowledge of Agni Finance, Merchant Moe, and Fluxion on Mantle, and Uniswap V3 and Trader Joe on Arbitrum.

You receive data about unusual wallet activity detected by a statistical anomaly model (z-score > 2.5 above 14-day baseline). Your job is to classify this signal and explain it in plain English for retail DeFi users.

CRITICAL: Respond with valid JSON ONLY. No preamble, no markdown fences, no explanation outside the JSON.

Required schema:
{
  "summary": "<2 sentences max. First: what happened. Second: why it matters. Plain English, no jargon.>",
  "confidence": <integer 0-100>,
  "signal_type": "<exactly one of: accumulation|distribution|whale_entry|whale_exit|unusual_volume>",
  "key_factors": ["<factor 1>", "<factor 2>", "<factor 3>"]
}

Confidence calibration:
- 85-100: Strong multi-wallet cluster (3+ wallets), z-score > 4.0, pattern matches known smart money behaviour
- 70-84:  Moderate cluster (2+ wallets), z-score 3.0-4.0, some supporting context
- 55-69:  Single wallet or z-score 2.5-3.0, limited corroborating signals
- 40-54:  Borderline — low volume, isolated event, ambiguous pattern
- < 40:   Discard — too noisy. Return confidence < 40 and I will not surface this signal.

Be conservative. A false positive erodes user trust more than a missed signal.
Do NOT speculate about price movements or give financial advice.
Focus on on-chain behaviour patterns only."""


USER_TEMPLATE = """On-chain anomaly detected on {chain} at {timestamp} UTC

PROTOCOL: {protocol}
POOL: {pool_address}
EVENT TYPE: {event_type}
TOTAL VOLUME: ${total_volume_usd:,.0f} USD
Z-SCORE vs 14-day baseline: {z_score:.2f}σ
DURATION: {duration}

WALLETS INVOLVED ({wallet_count}):
{wallet_details}

SIGNAL CONTEXT:
- Baseline mean for this pool/event type: ${baseline_mean:,.0f}/hour
- This event is {z_score:.1f}x the typical standard deviation above normal
- Pattern: {pattern_description}

Classify this signal. Return JSON only."""


def build_prompt(candidate: dict, wallet_details: str = "") -> str:
    """
    Build the user prompt from an AnomalyCandidate dict.
    wallet_details is optional Nansen-enriched wallet context.
    """
    from datetime import datetime

    wallets    = candidate.get("wallets", [])
    z_score    = float(candidate.get("z_score", 0))
    event_type = candidate.get("event_type", "swap")
    vol        = float(candidate.get("total_volume_usd", 0))

    # Determine pattern description
    wallet_count = len(wallets)
    if wallet_count >= 3 and z_score >= 4.0:
        pattern = f"Coordinated movement — {wallet_count} wallets acting in concert"
    elif wallet_count == 1 and z_score >= 4.0:
        pattern = "Single large wallet — potential whale entry/exit"
    elif event_type == "mint":
        pattern = "Liquidity being added — possible accumulation setup"
    elif event_type == "burn":
        pattern = "Liquidity being removed — possible distribution or exit"
    else:
        pattern = f"Unusual {event_type} activity above statistical baseline"

    # Duration
    first = candidate.get("first_seen", "")
    last  = candidate.get("last_seen", "")
    try:
        dt = (datetime.fromisoformat(last) - datetime.fromisoformat(first)).total_seconds()
        duration = f"{int(dt/60)} minutes" if dt >= 60 else f"{int(dt)} seconds"
    except Exception:
        duration = "unknown"

    # Wallet summary
    if not wallet_details:
        wallet_details = "\n".join(
            f"  {i+1}. {w[:12]}...{w[-6:]} (unknown label)"
            for i, w in enumerate(wallets[:5])
        )
        if len(wallets) > 5:
            wallet_details += f"\n  ... and {len(wallets)-5} more"

    return USER_TEMPLATE.format(
        chain               = candidate.get("chain", "mantle").capitalize(),
        timestamp           = candidate.get("detected_at", "")[:19],
        protocol            = candidate.get("protocol", "unknown"),
        pool_address        = candidate.get("pool_address", "0x???"),
        event_type          = event_type,
        total_volume_usd    = vol,
        z_score             = z_score,
        duration            = duration,
        wallet_count        = wallet_count,
        wallet_details      = wallet_details,
        baseline_mean       = vol / max(z_score, 1),   # rough back-calculation
        pattern_description = pattern,
    )
