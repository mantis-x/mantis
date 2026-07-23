"""
Telegram command handlers for Mantis Scout bot.

Commands:
  /start                — welcome + subscribe
  /subscribe [chain]    — subscribe (optional chain filter: mantle | arbitrum | hashkey | ethereum | all)
  /unsubscribe          — unsubscribe
  /status               — bot + market status
  /history              — last 5 signals
  /help                 — command list
  /verify <id>          — verify a signal on-chain
"""
from __future__ import annotations

import io
import logging
import os
import re

import qrcode
from telegram import Update
from telegram.ext import ContextTypes

from src.formatters.signal_card import (
    format_status_card,
    format_history_card,
)
from src.telegram.subscription_manager import SubscriptionManager
from src.common.live_stats import get_live_candidates, get_enrichment_consecutive_errors
from src.audit.on_chain_logger import get_explorer_contract_url

log = logging.getLogger(__name__)

SUPPORTED_CHAINS = {"mantle", "arbitrum", "hashkey", "ethereum"}
_CHAIN_LABEL = {"mantle": "Mantle", "arbitrum": "Arbitrum", "hashkey": "HashKey Chain", "ethereum": "Ethereum"}

# Pro tier billing — same env vars/defaults as
# packages/shared/src/billing/pro_payment_watcher.py (this package doesn't
# import from shared; see packages/delivery/src/db/models/subscription.py's
# own docstring for why it's a synced copy, not a shared import).
PRO_TIER_PAYMENTS_ENABLED  = os.getenv("PRO_TIER_PAYMENTS_ENABLED", "false").lower() == "true"
PRO_TIER_RECEIVE_ADDRESS   = os.getenv("PRO_TIER_RECEIVE_ADDRESS", "")
PRO_TIER_PRICE_USDC        = float(os.getenv("PRO_TIER_PRICE_USDC", "29"))  # 1-month price
PRO_TIER_DISCOUNT_6MO_PCT  = float(os.getenv("PRO_TIER_DISCOUNT_6MO_PCT", "5"))
PRO_TIER_DISCOUNT_12MO_PCT = float(os.getenv("PRO_TIER_DISCOUNT_12MO_PCT", "10"))
_EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _qr_png_bytes(data: str) -> io.BytesIO:
    """PNG-encode a QR code for `data` (e.g. a receiving address) as an
    in-memory file-like object, ready for Telegram's reply_photo."""
    img = qrcode.make(data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _pro_tier_price_table() -> str:
    six_mo  = PRO_TIER_PRICE_USDC * 6 * (1 - PRO_TIER_DISCOUNT_6MO_PCT / 100)
    twelve_mo = PRO_TIER_PRICE_USDC * 12 * (1 - PRO_TIER_DISCOUNT_12MO_PCT / 100)
    return (
        f"  1 month:  {PRO_TIER_PRICE_USDC:g} USDC\n"
        f"  6 months: {six_mo:g} USDC  ({PRO_TIER_DISCOUNT_6MO_PCT:g}% off)\n"
        f"  12 months: {twelve_mo:g} USDC  ({PRO_TIER_DISCOUNT_12MO_PCT:g}% off)"
    )

WELCOME_MSG = """🦟 <b>Welcome to Mantis Scout</b>

I monitor Mantle, Arbitrum, HashKey Chain &amp; Ethereum DeFi 24/7 and alert you when smart money moves.

<b>What I detect:</b>
  📈 Smart money accumulation
  🐋 Whale entries / exits
  ⚡ Unusual volume spikes

<b>Chains:</b> Mantle · Arbitrum · HashKey Chain · Ethereum
<b>Every signal is hashed on-chain — fully auditable.</b>

Use /subscribe to receive alerts from all chains.
Use /subscribe mantle, /subscribe arbitrum, /subscribe hashkey, or /subscribe ethereum to filter by chain.
Use /help to see all commands.

Free tier: 3 alerts/day · No credit card needed"""


HELP_MSG = """🦟 <b>Mantis Scout — Commands</b>

/subscribe [chain]  Start receiving signals (chain: mantle | arbitrum | hashkey | ethereum | all)
/unsubscribe        Stop receiving signals
/status             Bot status and stats
/history            Last 5 signals
/verify &lt;id&gt;       Verify a signal on-chain
/upgrade            Go Pro — unlimited alerts
/register_wallet &lt;address&gt;  Link the wallet you'll pay Pro from
/help               This message

<b>Chain filters:</b>
  /subscribe           → all chains
  /subscribe mantle    → Mantle only
  /subscribe arbitrum  → Arbitrum only
  /subscribe hashkey   → HashKey Chain only
  /subscribe ethereum  → Ethereum only

<b>Free tier:</b> 3 alerts/day
<b>Pro tier:</b> Unlimited alerts + Execute agent"""


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
                "Supported: <code>mantle</code>, <code>arbitrum</code>, <code>hashkey</code>, <code>ethereum</code>, or leave blank for all.\n"
                "Example: /subscribe arbitrum"
            )
            return

        is_new = sub_manager.subscribe(chat_id)
        sub_manager.set_chains(chat_id, chains)

        if is_new:
            msg = (
                f"✅ <b>Subscribed!</b> Receiving signals from <b>{display}</b>.\n\n"
                "Free tier: 3 alerts/day.\n\n"
                "Use /subscribe mantle, /subscribe arbitrum, /subscribe hashkey, or /subscribe ethereum to filter by chain.\n"
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
        enrichment_errors = await get_enrichment_consecutive_errors(redis_url)
        if enrichment_errors is not None:
            stats["enrichment_errors"] = enrichment_errors
        card = format_status_card(stats)
        sub = sub_manager.get_subscription(update.effective_chat.id)
        if sub and sub.is_pro:
            expiry = f" (until {sub.pro_expires_at:%Y-%m-%d})" if sub.pro_expires_at else " (no expiry)"
            card += f"\n\n💎 <b>Pro tier active</b>{expiry}"
        await update.message.reply_html(card)

    async def history(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        signals = sub_manager.get_history(5)
        await update.message.reply_html(format_history_card(signals))

    async def verify(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args
        if not args:
            links = "\n".join(
                f"• <a href='{get_explorer_contract_url(c)}'>{_CHAIN_LABEL[c]}</a>"
                for c in ("mantle", "arbitrum", "hashkey", "ethereum")
                if get_explorer_contract_url(c)
            )
            await update.message.reply_html(
                "Usage: /verify &lt;signal_id&gt;\n"
                "Example: /verify 42\n\n"
                "Find the ID on the alert itself (🆔 Signal #…) or with /history. "
                "Every signal is hashed on its origin chain — give an ID and I'll "
                "look up the right one, or browse SignalAuditLog directly:\n"
                f"{links}",
                disable_web_page_preview=True,
            )
            return

        signal_id = args[0]
        signal = sub_manager.get_signal_by_id(signal_id)

        if signal is None:
            links = "\n".join(
                f"• <a href='{get_explorer_contract_url(c)}'>{_CHAIN_LABEL[c]}</a>"
                for c in ("mantle", "arbitrum", "hashkey", "ethereum")
                if get_explorer_contract_url(c)
            )
            msg = (
                f"🔐 <b>Signal #{signal_id}</b>\n\n"
                "I don't have this one in recent history (older than the last 50 "
                "signals, or an invalid ID) — so I can't tell you which chain it's "
                "logged on. You can still browse SignalAuditLog directly:\n"
                f"{links}"
            )
            await update.message.reply_html(msg, disable_web_page_preview=True)
            return

        chain    = signal.get("chain", "mantle")
        label    = _CHAIN_LABEL.get(chain, chain.capitalize())
        explorer = get_explorer_contract_url(chain)
        msg = (
            f"🔐 <b>Signal #{signal_id} audit</b>\n\n"
            f"Every Mantis Scout signal is hashed with keccak256 "
            f"and recorded immutably — this one on {label}.\n\n"
            f"<a href='{explorer}'>View SignalAuditLog on {label} Explorer →</a>"
        )
        await update.message.reply_html(msg, disable_web_page_preview=True)

    async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_html(HELP_MSG)

    async def register_wallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        args = ctx.args or []
        if not args or not _EVM_ADDRESS_RE.match(args[0]):
            await update.message.reply_html(
                "Usage: /register_wallet &lt;address&gt;\n"
                "Example: /register_wallet 0xabc...123\n\n"
                "This is the wallet you'll send Pro-tier payment <b>from</b> on Arbitrum — "
                "used to match your payment automatically. It never needs to sign anything for me."
            )
            return
        if not sub_manager.is_subscribed(chat_id):
            sub_manager.subscribe(chat_id)
        sub_manager.register_wallet(chat_id, args[0])
        await update.message.reply_html(
            f"✅ Wallet registered: <code>{args[0]}</code>\n\nUse /upgrade to see payment details."
        )

    async def upgrade(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not PRO_TIER_PAYMENTS_ENABLED or not PRO_TIER_RECEIVE_ADDRESS:
            await update.message.reply_html(
                "🚧 <b>Pro tier isn't live yet.</b>\n\n"
                "The payment path is built but not switched on — hang tight, it's coming soon."
            )
            return
        chat_id = update.effective_chat.id
        sub = sub_manager.get_subscription(chat_id)
        if sub is None or not sub.registered_wallet:
            await update.message.reply_html(
                "First, register the wallet you'll pay from:\n"
                "/register_wallet &lt;address&gt;\n\n"
                "Then run /upgrade again to see payment details."
            )
            return
        caption = (
            f"💎 <b>Mantis Scout Pro</b>\n\n"
            f"{_pro_tier_price_table()}\n\n"
            f"Send the amount for whichever period you want (native USDC, Arbitrum One) "
            f"from your registered wallet <code>{sub.registered_wallet}</code> to:\n\n"
            f"<code>{PRO_TIER_RECEIVE_ADDRESS}</code>\n\n"
            "Pro is credited automatically within a few minutes of confirmation — "
            "no need to message anyone. Unlimited alerts + Execute agent access once active.\n\n"
            "⚠️ QR encodes the address only — select USDC and enter the amount yourself in your wallet app."
        )
        await update.message.reply_photo(
            photo=_qr_png_bytes(PRO_TIER_RECEIVE_ADDRESS),
            caption=caption,
            parse_mode="HTML",
        )

    app.add_handler(CommandHandler("start",       start))
    app.add_handler(CommandHandler("subscribe",   subscribe))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))
    app.add_handler(CommandHandler("status",      status))
    app.add_handler(CommandHandler("history",     history))
    app.add_handler(CommandHandler("verify",      verify))
    app.add_handler(CommandHandler("help",        help_cmd))
    app.add_handler(CommandHandler("register_wallet", register_wallet))
    app.add_handler(CommandHandler("upgrade",     upgrade))

    log.info("Registered 9 command handlers")
