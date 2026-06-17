"""
Discord command handlers for Mantis Scout bot.

Commands (prefix "!"):
  !subscribe   — subscribe this channel to signals
  !unsubscribe — unsubscribe
  !status      — bot + market status
  !history     — last 5 signals
  !verify <id> — verify a signal on-chain
  !help        — command list
"""
from __future__ import annotations

import logging

from src.formatters.signal_card import (
    format_status_card_plain,
    format_history_card_plain,
)
from src.common.subscription_manager import SubscriptionManager

log = logging.getLogger(__name__)

WELCOME_MSG = """**Welcome to Mantis Scout**

I monitor Mantle DeFi 24/7 and alert you when smart money moves.

**What I detect:**
  Smart money accumulation
  Whale entries / exits
  Unusual volume spikes

Every signal is hashed on Mantle — fully auditable.

Use `!subscribe` to start receiving alerts in this channel.
Use `!help` to see all commands.

Free tier: 3 alerts/day · No credit card needed"""


HELP_MSG = """**Mantis Scout — Commands**

`!subscribe`    Start receiving signals in this channel
`!unsubscribe`  Stop receiving signals
`!status`       Bot status and stats
`!history`      Last 5 signals
`!verify <id>`  Verify a signal on-chain
`!help`         This message

**Free tier:** 3 alerts/day
**Pro tier:** Unlimited alerts + Execute agent

github.com/mantis-x/mantis"""

EXPLORER_CONTRACT = "https://explorer.mantle.xyz/address/0xd745Fc0c28B8755b6280232a179e21C50B1D3adf"


def register_handlers(bot, sub_manager: SubscriptionManager, stats: dict) -> None:
    """Register all command handlers with the discord.py Bot."""

    @bot.command(name="subscribe")
    async def subscribe(ctx) -> None:
        channel_id = str(ctx.channel.id)
        is_new     = sub_manager.subscribe(channel_id)
        if is_new:
            msg = (
                "**Subscribed!**\n\n"
                "This channel will receive Mantis Scout signals as they're detected.\n"
                "Free tier: 3 alerts/day.\n\n"
                "Use `!unsubscribe` to stop at any time."
            )
        else:
            msg = "This channel is already subscribed. Use `!unsubscribe` to stop."
        await ctx.send(msg)
        log.info("!subscribe from channel_id=%s", channel_id)

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
        await ctx.send(format_status_card_plain(stats))

    @bot.command(name="history")
    async def history(ctx) -> None:
        signals = sub_manager.get_history(5)
        await ctx.send(format_history_card_plain(signals))

    @bot.command(name="verify")
    async def verify(ctx, signal_id: str = "") -> None:
        if not signal_id:
            await ctx.send(
                "Usage: `!verify <signal_id>`\n"
                "Example: `!verify 42`\n\n"
                f"Check the signal's on-chain hash at:\n{EXPLORER_CONTRACT}"
            )
            return
        msg = (
            f"**Signal #{signal_id} audit**\n\n"
            "Every Mantis Scout signal is hashed with keccak256 "
            "and recorded immutably on Mantle.\n\n"
            f"View SignalAuditLog on Mantle Explorer: {EXPLORER_CONTRACT}"
        )
        await ctx.send(msg)

    @bot.command(name="help")
    async def help_cmd(ctx) -> None:
        await ctx.send(HELP_MSG)

    @bot.event
    async def on_ready() -> None:
        log.info("Discord bot logged in as %s", bot.user)

    log.info("Registered Discord command handlers")
