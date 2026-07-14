"""
Telegram command handlers for Mantis Scout bot.

Commands:
  /start                — welcome + subscribe
  /subscribe [chain]    — subscribe (optional chain filter: mantle | arbitrum | hashkey | all)
  /unsubscribe          — unsubscribe
  /status               — bot + market status
  /history              — last 5 signals
  /help                 — command list
  /verify <id>          — verify a signal on-chain
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from src.formatters.signal_card import (
    format_status_card,
    format_history_card,
)
from src.telegram.subscription_manager import SubscriptionManager
from src.common.live_stats import get_live_candidates

log = logging.getLogger(__name__)

SUPPORTED_CHAINS = {"mantle", "arbitrum", "hashkey"}

WELCOME_MSG = """🦟 <b>Welcome to Mantis Scout</b>

I monitor Mantle, Arbitrum &amp; HashKey Chain DeFi 24/7 and alert you when smart money moves.

<b>What I detect:</b>
  📈 Smart money accumulation
  🐋 Whale entries / exits
  ⚡ Unusual volume spikes

<b>Chains:</b> Mantle · Arbitrum · HashKey Chain
<b>Every signal is hashed on-chain — fully auditable.</b>

Use /subscribe to receive alerts from all chains.
Use /subscribe mantle, /subscribe arbitrum, or /subscribe hashkey to filter by chain.
Use /help to see all commands.

Free tier: 3 alerts/day · No credit card needed"""


HELP_MSG = """🦟 <b>Mantis Scout — Commands</b>

/subscribe [chain]  Start receiving signals (chain: mantle | arbitrum | hashkey | all)
/unsubscribe        Stop receiving signals
/status             Bot status and stats
/history            Last 5 signals
/verify &lt;id&gt;       Verify a signal on-chain
/help               This message

<b>Chain filters:</b>
  /subscribe           → all chains
  /subscribe mantle    → Mantle only
  /subscribe arbitrum  → Arbitrum only
  /subscribe hashkey   → HashKey Chain only

<b>Free tier:</b> 3 alerts/day
<b>Pro tier:</b> Unlimited alerts + Execute agent

<i>github.com/mantis-x/mantis</i>"""


def _parse_chain_arg(args: list[str]) -> tuple[str | None, str]:
    """
    Parse optional chain argument from command args.
    Returns (chains_set_or_None, display_text).
    """
    if not args:
        return None, "all chains"
    chain = args[0].lower()
    if chain == "all":
        return None, "all chains"
    if chain in SUPPORTED_CHAINS:
        return {chain}, chain
    return "invalid", chain


def register_handlers(app, sub_manager: SubscriptionManager, stats: dict, redis_url: str = "") -> None:
    """Register all command handlers with the Application."""
    from telegram.ext import CommandHandler

    async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        sub_manager.subscribe(chat_id)
        await update.message.reply_html(WELCOME_MSG)
        log.info("/start from chat_id=%d", chat_id)

    async def subscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        chains, display = _parse_chain_arg(ctx.args or [])

        if chains == "invalid":
            await update.message.reply_html(
                f"Unknown chain <b>{display}</b>.\n"
                "Supported: <code>mantle</code>, <code>arbitrum</code>, <code>hashkey</code>, or leave blank for all.\n"
                "Example: /subscribe arbitrum"
            )
            return

        is_new = sub_manager.subscribe(chat_id)
        sub_manager.set_chains(chat_id, chains)

        if is_new:
            msg = (
                f"✅ <b>Subscribed!</b> Receiving signals from <b>{display}</b>.\n\n"
                "Free tier: 3 alerts/day.\n\n"
                "Use /subscribe mantle, /subscribe arbitrum, or /subscribe hashkey to filter by chain.\n"
                "Use /unsubscribe to stop at any time."
            )
        else:
            msg = f"✅ Chain filter updated → <b>{display}</b>."
        await update.message.reply_html(msg)

    async def unsubscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        removed = sub_manager.unsubscribe(chat_id)
        if removed:
            msg = "👋 <b>Unsubscribed.</b>\nYou won't receive any more signals.\nUse /subscribe to re-subscribe."
        else:
            msg = "You weren't subscribed. Use /subscribe to start."
        await update.message.reply_html(msg)

    async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        stats["subscribers"] = sub_manager.subscriber_count()
        live_candidates = await get_live_candidates(redis_url)
        if live_candidates is not None:
            stats["candidates"] = live_candidates
        await update.message.reply_html(format_status_card(stats))

    async def history(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        signals = sub_manager.get_history(5)
        await update.message.reply_html(format_history_card(signals))

    async def verify(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args
        if not args:
            await update.message.reply_html(
                "Usage: /verify &lt;signal_id&gt;\n"
                "Example: /verify 42\n\n"
                "Check the signal's on-chain hash at:\n"
                "https://explorer.mantle.xyz/address/0xd745Fc0c28B8755b6280232a179e21C50B1D3adf"
            )
            return

        signal_id = args[0]
        explorer  = "https://explorer.mantle.xyz/address/0xd745Fc0c28B8755b6280232a179e21C50B1D3adf"
        msg = (
            f"🔐 <b>Signal #{signal_id} audit</b>\n\n"
            f"Every Mantis Scout signal is hashed with keccak256 "
            f"and recorded immutably on Mantle.\n\n"
            f"<a href='{explorer}'>View SignalAuditLog on Mantle Explorer →</a>"
        )
        await update.message.reply_html(msg, disable_web_page_preview=True)

    async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_html(HELP_MSG)

    app.add_handler(CommandHandler("start",       start))
    app.add_handler(CommandHandler("subscribe",   subscribe))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))
    app.add_handler(CommandHandler("status",      status))
    app.add_handler(CommandHandler("history",     history))
    app.add_handler(CommandHandler("verify",      verify))
    app.add_handler(CommandHandler("help",        help_cmd))

    log.info("Registered 7 command handlers")
