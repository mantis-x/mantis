"""
SignalCard — formats a Signal dict into a Telegram/Discord/LINE message.

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
    "uniswap_v3":    "Uniswap V3",
    "trader_joe":    "Trader Joe",
    "gmx":           "GMX",
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

# Per-chain explorer base URLs and display names
CHAIN_META = {
    "mantle":   ("https://explorer.mantle.xyz",  "Mantle"),
    "arbitrum": ("https://arbiscan.io",           "Arbitrum"),
    "hashkey":  ("https://hsk.blockscout.com",    "HashKey Chain"),
}


def _chain_meta(chain: str) -> tuple[str, str]:
    """Return (explorer_base, display_name) for a chain slug."""
    return CHAIN_META.get(chain, (f"https://{chain}.explorer", chain.capitalize()))


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
    """Format a signal dict into a Telegram HTML message card."""
    signal_id    = signal.get("id")
    signal_type  = signal.get("signal_type", "unusual_volume")
    chain        = signal.get("chain", "mantle")
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
    explorer_base, chain_display = _chain_meta(chain)

    pool_short   = f"{pool_address[:6]}...{pool_address[-4:]}" if pool_address else "unknown"
    explorer_url = f"{explorer_base}/address/{pool_address}"

    lines = [
        f"{emoji} <b>Mantis Scout — {label}</b>",
        f"<i>{proto_name} · {chain_display}</i>",
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

    if audit_tx_hash:
        audit_url = f"{explorer_base}/tx/{audit_tx_hash}"
        lines.append(f"🔐 <b>On-chain proof</b>  <a href='{audit_url}'>verify signal</a>")
    else:
        lines.append(f"🔐 <b>Pool</b>  <a href='{explorer_url}'>{pool_short}</a>")

    if signal_id is not None:
        lines.append(f"🆔 Signal #{signal_id} — verify anytime with <code>/verify {signal_id}</code>")

    lines.append("")
    lines.append("⚠️ <i>Not financial advice. DYOR.</i>")
    lines.append("<i>Mantis Scout · mantis-x/mantis</i>")

    return "\n".join(lines)


def format_signal_card_markdown(signal: dict, audit_tx_hash: Optional[str] = None) -> str:
    """Format a signal dict for Discord (Markdown bold, no HTML)."""
    signal_id    = signal.get("id")
    signal_type  = signal.get("signal_type", "unusual_volume")
    chain        = signal.get("chain", "mantle")
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
    explorer_base, chain_display = _chain_meta(chain)

    pool_short   = f"{pool_address[:6]}...{pool_address[-4:]}" if pool_address else "unknown"
    explorer_url = f"{explorer_base}/address/{pool_address}"

    lines = [
        f"{emoji} **Mantis Scout — {label}**",
        f"_{proto_name} · {chain_display}_",
        "",
        f"**Signal**  {confidence_bar(confidence)}",
        f"**Volume**  {format_usd(volume_usd)} ({event_type})",
        f"**Z-score**  {z_score:.2f}σ above 14-day baseline",
        f"**Wallets**  {len(wallets)} detected",
        "",
        "📋 **What happened**",
        summary,
        "",
    ]

    if key_factors:
        lines.append("🔎 **Key factors**")
        for factor in key_factors[:3]:
            lines.append(f"  • {factor}")
        lines.append("")

    if audit_tx_hash:
        audit_url = f"{explorer_base}/tx/{audit_tx_hash}"
        lines.append(f"🔐 **On-chain proof**  verify signal: {audit_url}")
    else:
        lines.append(f"🔐 **Pool** {pool_short}  {explorer_url}")

    if signal_id is not None:
        lines.append(f"🆔 Signal #{signal_id} — verify anytime with `!verify {signal_id}`")

    lines.append("")
    lines.append("⚠️ _Not financial advice. DYOR._")
    lines.append("_Mantis Scout · mantis-x/mantis_")

    return "\n".join(lines)


def format_signal_card_plain(signal: dict, audit_tx_hash: Optional[str] = None) -> str:
    """Format a signal dict as plain text — for LINE Messaging API."""
    signal_id    = signal.get("id")
    signal_type  = signal.get("signal_type", "unusual_volume")
    chain        = signal.get("chain", "mantle")
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
    explorer_base, chain_display = _chain_meta(chain)

    pool_short   = f"{pool_address[:6]}...{pool_address[-4:]}" if pool_address else "unknown"
    explorer_url = f"{explorer_base}/address/{pool_address}"

    lines = [
        f"{emoji} Mantis Scout — {label}",
        f"{proto_name} · {chain_display}",
        "",
        f"Signal  {confidence_bar(confidence)}",
        f"Volume  {format_usd(volume_usd)} ({event_type})",
        f"Z-score  {z_score:.2f}σ above 14-day baseline",
        f"Wallets  {len(wallets)} detected",
        "",
        "What happened:",
        summary,
        "",
    ]

    if key_factors:
        lines.append("Key factors:")
        for factor in key_factors[:3]:
            lines.append(f"  • {factor}")
        lines.append("")

    if audit_tx_hash:
        audit_url = f"{explorer_base}/tx/{audit_tx_hash}"
        lines.append(f"On-chain proof — verify signal: {audit_url}")
    else:
        lines.append(f"Pool: {pool_short}  {explorer_url}")

    if signal_id is not None:
        lines.append(f'Signal #{signal_id} — verify anytime with "verify {signal_id}"')

    lines.append("")
    lines.append("Not financial advice. DYOR.")
    lines.append("Mantis Scout · mantis-x/mantis")

    return "\n".join(lines)


def format_status_card(stats: dict) -> str:
    """Format a /status response."""
    return (
        "🦟 <b>Mantis Scout — Status</b>\n"
        "\n"
        f"🟢 <b>Live</b> — {stats.get('chains_label', 'Mantle')}\n"
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
        chain    = s.get("chain", "mantle").capitalize()
        sig_id   = s.get("id")
        id_part  = f" · <code>#{sig_id}</code>" if sig_id is not None else ""
        lines.append(f"{i}. {emoji} <b>{sig_type}</b> · {proto} · {chain} · {vol} · {conf}% conf{id_part}")

    return "\n".join(lines)


def format_status_card_plain(stats: dict) -> str:
    """Plain-text /status response — for LINE / Discord."""
    return (
        "Mantis Scout — Status\n"
        "\n"
        f"Live — {stats.get('chains_label', 'Mantle')}\n"
        f"Pools tracked: {stats.get('pools', 11)}\n"
        f"Signals today: {stats.get('signals_today', 0)}\n"
        f"Candidates scored: {stats.get('candidates', 0)}\n"
        "\n"
        "Commands: subscribe, unsubscribe, history, help"
    )


def format_history_card_plain(signals: list) -> str:
    """Plain-text /history response — for LINE / Discord."""
    if not signals:
        return "No signals yet. Mantis Scout is watching the market."

    lines = ["Recent signals:\n"]
    for i, s in enumerate(signals[:5], 1):
        sig_type = SIGNAL_LABELS.get(s.get("signal_type", ""), "Signal")
        conf     = int(s.get("confidence", 0))
        proto    = PROTOCOL_NAMES.get(s.get("protocol", ""), "Unknown")
        vol      = format_usd(float(s.get("total_volume_usd", 0)))
        emoji    = SIGNAL_EMOJIS.get(s.get("signal_type", ""), "🔍")
        chain    = s.get("chain", "mantle").capitalize()
        sig_id   = s.get("id")
        id_part  = f" · #{sig_id}" if sig_id is not None else ""
        lines.append(f"{i}. {emoji} {sig_type} · {proto} · {chain} · {vol} · {conf}% conf{id_part}")

    return "\n".join(lines)
