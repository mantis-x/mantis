"""
Telegram command handlers for Mantis Scout bot.

Commands:
  /start       — welcome + subscribe
  /subscribe   — subscribe to signals
  /unsubscribe — unsubscribe
  /status      — bot + market status
  /history     — last 5 signals
  /help        — command list
  /verify <id> — verify a signal on-chain
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

log = logging.getLogger(__name__)

WELCOME_MSG = """🦟 <b>Welcome to Mantis Scout</b>

I monitor Mantle DeFi 24/7 and alert you when smart money moves.

<b>What I detect:</b>
  📈 Smart money accumulation
  🐋 Whale entries / exits
  ⚡ Unusual volume spikes

<b>Every signal is hashed on Mantle — fully auditable.</b>

Use /subscribe to start receiving alerts.
Use /help to see all commands.

Free tier: 3 alerts/day · No credit card needed"""


HELP_MSG = """🦟 <b>Mantis Scout — Commands</b>

/subscribe    Start receiving signals
/unsubscribe  Stop receiving signals
/status       Bot status and stats
/history      Last 5 signals
/verify &lt;id&gt;  Verify a signal on-chain
/help         This message

<b>Free tier:</b> 3 alerts/day
<b>Pro tier:</b> Unlimited alerts + Execute agent

<i>github.com/mantis-x/mantis</i>"""


def register_handlers(app, sub_manager: SubscriptionManager, stats: dict) -> None:
    """Register all command handlers with the Application."""
    from telegram.ext import CommandHandler

    async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        sub_manager.subscribe(chat_id)
        await update.message.reply_html(WELCOME_MSG)
        log.info("/start from chat_id=%d", chat_id)

    async def subscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        is_new  = sub_manager.subscribe(chat_id)
        if is_new:
            msg = (
                "✅ <b>Subscribed!</b>\n\n"
                "You'll receive Mantis Scout signals as they're detected.\n"
                "Free tier: 3 alerts/day.\n\n"
                "Use /unsubscribe to stop at any time."
            )
        else:
            msg = "✅ You're already subscribed. Use /unsubscribe to stop."
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
        explorer  = f"https://explorer.mantle.xyz/address/0xd745Fc0c28B8755b6280232a179e21C50B1D3adf"
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
