"""
SignalCard — formats a Signal dict into a Telegram message.

Design principles:
- Plain English first, no crypto jargon in the first line
- Confidence shown as a visual bar so it reads at a glance
- Audit link always included — this is our core differentiator
- Fits in one Telegram message (< 4096 chars)
"""
from __future__ import annotations
from typing import Optional


PROTOCOL_NAMES = {
    "agni_finance":  "Agni Finance",
    "merchant_moe":  "Merchant Moe",
    "fluxion":       "Fluxion",
}

SIGNAL_EMOJIS = {
    "accumulation":   "📈",
    "distribution":   "📉",
    "whale_entry":    "🐋",
    "whale_exit":     "🚨",
    "unusual_volume": "⚡",
}

SIGNAL_LABELS = {
    "accumulation":   "Accumulation",
    "distribution":   "Distribution",
    "whale_entry":    "Whale Entry",
    "whale_exit":     "Whale Exit",
    "unusual_volume": "Unusual Volume",
}


def confidence_bar(score: int) -> str:
    """Visual confidence bar: ████████░░ 82%"""
    filled  = round(score / 10)
    empty   = 10 - filled
    color   = "🟢" if score >= 75 else "🟡" if score >= 55 else "🔴"
    bar     = "█" * filled + "░" * empty
    return f"{color} {bar} {score}%"


def format_usd(amount: float) -> str:
    if amount >= 1_000_000:
        return f"${amount/1_000_000:.1f}M"
    if amount >= 1_000:
        return f"${amount/1_000:.0f}K"
    return f"${amount:.0f}"


def format_signal_card(signal: dict, audit_tx_hash: Optional[str] = None) -> str:
    """
    Format a signal dict into a Telegram HTML message card.
    Returns a string ready to send with parse_mode="HTML".
    """
    signal_type  = signal.get("signal_type", "unusual_volume")
    protocol     = signal.get("protocol", "unknown")
    confidence   = int(signal.get("confidence", 0))
    summary      = signal.get("summary", "Unusual on-chain activity detected.")
    key_factors  = signal.get("key_factors", [])
    z_score      = float(signal.get("z_score", 0))
    volume_usd   = float(signal.get("total_volume_usd", 0))
    event_type   = signal.get("event_type", "swap")
    pool_address = signal.get("pool_address", "")
    wallets      = signal.get("wallets", [])

    emoji        = SIGNAL_EMOJIS.get(signal_type, "🔍")
    label        = SIGNAL_LABELS.get(signal_type, "Signal")
    proto_name   = PROTOCOL_NAMES.get(protocol, protocol.title())

    # Explorer links
    pool_short   = f"{pool_address[:6]}...{pool_address[-4:]}" if pool_address else "unknown"
    explorer_url = f"https://explorer.mantle.xyz/address/{pool_address}"

    lines = [
        f"{emoji} <b>Mantis Scout — {label}</b>",
        f"<i>{proto_name} · Mantle</i>",
        "",
        f"<b>Signal</b>  {confidence_bar(confidence)}",
        f"<b>Volume</b>  {format_usd(volume_usd)} ({event_type})",
        f"<b>Z-score</b>  {z_score:.2f}σ above 14-day baseline",
        f"<b>Wallets</b>  {len(wallets)} detected",
        "",
        f"📋 <b>What happened</b>",
        f"{summary}",
        "",
    ]

    if key_factors:
        lines.append("🔎 <b>Key factors</b>")
        for factor in key_factors[:3]:
            lines.append(f"  • {factor}")
        lines.append("")

    # Audit link
    if audit_tx_hash:
        audit_url = f"https://explorer.mantle.xyz/tx/{audit_tx_hash}"
        lines.append(f"🔐 <b>On-chain proof</b>  <a href='{audit_url}'>verify signal</a>")
    else:
        lines.append(f"🔐 <b>Pool</b>  <a href='{explorer_url}'>{pool_short}</a>")

    lines.append("")
    lines.append("⚠️ <i>Not financial advice. DYOR.</i>")
    lines.append("<i>Mantis Scout · mantis-x/mantis</i>")

    return "\n".join(lines)


def format_status_card(stats: dict) -> str:
    """Format a /status response."""
    return (
        "🦟 <b>Mantis Scout — Status</b>\n"
        "\n"
        f"🟢 <b>Live</b> — Mantle mainnet\n"
        f"📦 Pools tracked: <b>{stats.get('pools', 11)}</b>\n"
        f"⚡ Signals today: <b>{stats.get('signals_today', 0)}</b>\n"
        f"🎯 Candidates scored: <b>{stats.get('candidates', 0)}</b>\n"
        "\n"
        "Commands: /subscribe /unsubscribe /history /help"
    )


def format_history_card(signals: list) -> str:
    """Format a /history response with last N signals."""
    if not signals:
        return "📭 No signals yet. Mantis Scout is watching the market."

    lines = ["📚 <b>Recent signals</b>\n"]
    for i, s in enumerate(signals[:5], 1):
        sig_type = SIGNAL_LABELS.get(s.get("signal_type", ""), "Signal")
        conf     = int(s.get("confidence", 0))
        proto    = PROTOCOL_NAMES.get(s.get("protocol", ""), "Unknown")
        vol      = format_usd(float(s.get("total_volume_usd", 0)))
        emoji    = SIGNAL_EMOJIS.get(s.get("signal_type", ""), "🔍")
        lines.append(f"{i}. {emoji} <b>{sig_type}</b> · {proto} · {vol} · {conf}% conf")

    return "\n".join(lines)
