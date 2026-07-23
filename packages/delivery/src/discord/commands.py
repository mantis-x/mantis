"""
Discord command handlers for Mantis Scout bot.

Commands (prefix "!"):
  !subscribe [chain]   — subscribe this channel (chain: mantle | arbitrum | hashkey | ethereum | all)
  !unsubscribe         — unsubscribe
  !status              — bot + market status
  !history             — last 5 signals
  !verify <id>         — verify a signal on-chain
  !help                — command list
"""
from __future__ import annotations

import logging

from src.formatters.signal_card import (
    format_status_card_plain,
    format_history_card_plain,
)
from src.common.subscription_manager import SubscriptionManager
from src.common.live_stats import get_live_candidates, get_enrichment_consecutive_errors
from src.audit.on_chain_logger import get_explorer_contract_url

log = logging.getLogger(__name__)

SUPPORTED_CHAINS = {"mantle", "arbitrum", "hashkey", "ethereum"}
_CHAIN_LABEL = {"mantle": "Mantle", "arbitrum": "Arbitrum", "hashkey": "HashKey Chain", "ethereum": "Ethereum"}

WELCOME_MSG = """**Welcome to Mantis Scout**

I monitor Mantle, Arbitrum, HashKey Chain & Ethereum DeFi 24/7 and alert you when smart money moves.

**What I detect:**
  Smart money accumulation
  Whale entries / exits
  Unusual volume spikes

**Chains:** Mantle · Arbitrum · HashKey Chain · Ethereum
Every signal is hashed on-chain — fully auditable.

Use `!subscribe` to receive alerts from all chains.
Use `!subscribe mantle`, `!subscribe arbitrum`, `!subscribe hashkey`, or `!subscribe ethereum` to filter by chain.
Use `!help` to see all commands.

Free tier: 3 alerts/day · No credit card needed"""


HELP_MSG = """**Mantis Scout — Commands**

`!subscribe [chain]`  Start receiving signals (chain: mantle | arbitrum | hashkey | ethereum | all)
`!unsubscribe`        Stop receiving signals
`!status`             Bot status and stats
`!history`            Last 5 signals
`!verify <id>`        Verify a signal on-chain
`!help`               This message

**Chain filters:**
  `!subscribe`           → all chains
  `!subscribe mantle`    → Mantle only
  `!subscribe arbitrum`  → Arbitrum only
  `!subscribe hashkey`   → HashKey Chain only
  `!subscribe ethereum`  → Ethereum only

**Free tier:** 3 alerts/day
**Pro tier:** Unlimited alerts + Execute agent"""

def _parse_chain_arg(arg: str) -> tuple:
    """Returns (chains_set_or_None, display_text, error_or_None)."""
    arg = arg.strip().lower()
    if not arg or arg == "all":
        return None, "all chains", None
    if arg in SUPPORTED_CHAINS:
        return {arg}, arg, None
    return "invalid", arg, f"Unknown chain `{arg}`. Supported: `mantle`, `arbitrum`, `hashkey`, `ethereum`, or leave blank for all."


def register_handlers(bot, sub_manager: SubscriptionManager, stats: dict, redis_url: str = "") -> None:
    """Register all command handlers with the discord.py Bot."""

    @bot.command(name="subscribe")
    async def subscribe(ctx, chain: str = "") -> None:
        channel_id = str(ctx.channel.id)
        chains, display, error = _parse_chain_arg(chain)

        if error:
            await ctx.send(error)
            return

        is_new = sub_manager.subscribe(channel_id)
        sub_manager.set_chains(channel_id, chains)

        if is_new:
            msg = (
                f"**Subscribed!** Receiving signals from **{display}**.\n\n"
                "Free tier: 3 alerts/day.\n\n"
                "Use `!subscribe mantle`, `!subscribe arbitrum`, `!subscribe hashkey`, or `!subscribe ethereum` to filter by chain.\n"
                "Use `!unsubscribe` to stop at any time."
            )
        else:
            msg = f"Chain filter updated → **{display}**."
        await ctx.send(msg)
        log.info("!subscribe from channel_id=%s chains=%s", channel_id, chains)

    @bot.command(name="unsubscribe")
    async def unsubscribe(ctx) -> None:
        channel_id = str(ctx.channel.id)
        removed    = sub_manager.unsubscribe(channel_id)
        if removed:
            msg = "Unsubscribed. This channel won't receive any more signals.\nUse `!subscribe` to re-subscribe."
        else:
            msg = "This channel wasn't subscribed. Use `!subscribe` to start."
        await ctx.send(msg)

    @bot.command(name="status")
    async def status(ctx) -> None:
        stats["discord_subscribers"] = sub_manager.subscriber_count()
        live_candidates = await get_live_candidates(redis_url)
        if live_candidates is not None:
            stats["candidates"] = live_candidates
        enrichment_errors = await get_enrichment_consecutive_errors(redis_url)
        if enrichment_errors is not None:
            stats["enrichment_errors"] = enrichment_errors
        await ctx.send(format_status_card_plain(stats))

    @bot.command(name="history")
    async def history(ctx) -> None:
        signals = sub_manager.get_history(5)
        await ctx.send(format_history_card_plain(signals))

    @bot.command(name="verify")
    async def verify(ctx, signal_id: str = "") -> None:
        links = "\n".join(
            f"{_CHAIN_LABEL[c]}: {get_explorer_contract_url(c)}"
            for c in ("mantle", "arbitrum", "hashkey", "ethereum")
            if get_explorer_contract_url(c)
        )

        if not signal_id:
            await ctx.send(
                "Usage: `!verify <signal_id>`\n"
                "Example: `!verify 42`\n\n"
                "Find the ID on the alert itself (🆔 Signal #…) or with `!history`. "
                "Every signal is hashed on its origin chain — give an ID and I'll "
                f"look up the right one, or browse SignalAuditLog directly:\n{links}"
            )
            return

        signal = sub_manager.get_signal_by_id(signal_id)
        if signal is None:
            msg = (
                f"**Signal #{signal_id}**\n\n"
                "I don't have this one in recent history (older than the last 50 "
                "signals, or an invalid ID) — so I can't tell you which chain it's "
                f"logged on. You can still browse SignalAuditLog directly:\n{links}"
            )
            await ctx.send(msg)
            return

        chain    = signal.get("chain", "mantle")
        label    = _CHAIN_LABEL.get(chain, chain.capitalize())
        explorer = get_explorer_contract_url(chain)
        msg = (
            f"**Signal #{signal_id} audit**\n\n"
            "Every Mantis Scout signal is hashed with keccak256 "
            f"and recorded immutably — this one on {label}.\n\n"
            f"View SignalAuditLog on {label} Explorer: {explorer}"
        )
        await ctx.send(msg)

    @bot.command(name="help")
    async def help_cmd(ctx) -> None:
        await ctx.send(HELP_MSG)

    @bot.event
    async def on_ready() -> None:
        log.info("Discord bot logged in as %s", bot.user)

    log.info("Registered Discord command handlers")
