"""
Delivery Worker — entry point for Mantis Scout bots.
Run: python -m src.worker (from packages/delivery/)

Runs concurrent tasks:
  1. Telegram bot   — handles user commands (always on, requires TELEGRAM_BOT_TOKEN)
  2. Discord bot    — handles user commands (optional, requires DISCORD_BOT_TOKEN)
  3. LINE bot        — webhook server for user commands (optional, requires
                        LINE_CHANNEL_ACCESS_TOKEN + LINE_CHANNEL_SECRET)
  4. Signal dispatcher — polls Redis mantis:signals and pushes to subscribers
                          on every enabled platform

Redis key:
  INPUT: mantis:signals  (enrichment worker writes here)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".env"
))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mantis.delivery")

from telegram import Bot
from telegram.ext import Application
from telegram.error import TelegramError

from src.telegram.commands import register_handlers
from src.telegram.subscription_manager import SubscriptionManager
from src.formatters.signal_card import (
    format_signal_card,
    format_signal_card_markdown,
    format_signal_card_plain,
)
from src.audit.on_chain_logger import OnChainLogger

# Chain display name + pool_registry size, kept in sync with
# packages/ingestion/src/chains.py (no cross-package import to avoid
# coupling the delivery container's build to ingestion's source tree).
_CHAIN_DISPLAY = {
    "mantle":   "Mantle",
    "arbitrum": "Arbitrum",
    "hashkey":  "HashKey Chain",
    "ethereum": "Ethereum",
}
_CHAIN_POOL_COUNTS = {
    "mantle":   11,
    "arbitrum": 9,
    "hashkey":  3,
    "ethereum": 3,
}
_enabled_chains = [c.strip().lower() for c in os.getenv("CHAINS", "mantle").split(",") if c.strip()]
_chains_label   = " · ".join(_CHAIN_DISPLAY.get(c, c.capitalize()) for c in _enabled_chains) or "Mantle"
_pools_total    = sum(_CHAIN_POOL_COUNTS.get(c, 0) for c in _enabled_chains) or 11

# Shared state
sub_manager = SubscriptionManager()
stats       = {
    "signals_today": 0,
    "candidates":    0,
    "pools":         _pools_total,
    "chains_label":  _chains_label,
    "subscribers":   0,
    "started_at":    time.time(),
}


async def dispatch_signals(
    bot: Bot,
    redis_url: str,
    discord_bot=None,
    line_messaging_api=None,
) -> None:
    """
    Continuously poll Redis for new signals and push to subscribers
    across every enabled platform (Telegram, Discord, LINE).
    Runs as a background task alongside the chat bots.
    """
    import redis.asyncio as aioredis
    r = aioredis.from_url(redis_url, decode_responses=True)

    # Import Discord/LINE only when actually enabled -- these were previously
    # imported unconditionally here even though Discord/LINE are optional and
    # skipped elsewhere in this file when their tokens aren't set. If either
    # package has any import-time issue in the deployed image, an
    # unconditional import here silently kills this entire background task
    # before its first log line -- meaning Telegram delivery (which does not
    # depend on either package) would break too, for a completely unrelated
    # reason, with no error anywhere. Scope the imports to match how main()
    # already gates these platforms.
    discord_sub_manager = None
    send_discord         = None
    if discord_bot is not None:
        from src.discord.bot import discord_sub_manager, send_signal_to_subscribers as send_discord

    line_sub_manager = None
    send_line        = None
    if line_messaging_api is not None:
        from src.line.bot import line_sub_manager, send_signal_to_subscribers as send_line

    # On-chain audit loggers are built lazily, one per origin chain, since
    # not every chain necessarily has a deployed SignalAuditLog yet.
    audit_loggers: dict[str, OnChainLogger | None] = {}

    def get_audit_logger(chain: str) -> OnChainLogger | None:
        if chain not in audit_loggers:
            try:
                audit_loggers[chain] = OnChainLogger(chain=chain)
                log.info("Audit logger ready — chain=%s", chain)
            except Exception as exc:
                log.warning(
                    "Audit logger unavailable for chain=%s: %s — "
                    "signals on this chain will dispatch without on-chain logging",
                    chain, exc,
                )
                audit_loggers[chain] = None
        return audit_loggers[chain]

    log.info("Signal dispatcher ready — listening on mantis:signals")

    while True:
        try:
            item = await r.brpop("mantis:signals", timeout=5)
            if item is None:
                continue

            _, payload = item
            signal     = json.loads(payload)

            # Add to history
            sub_manager.add_to_history(signal)
            stats["signals_today"] += 1

            # Format the Telegram card
            message = format_signal_card(signal)

            # Get eligible subscribers
            recipients = sub_manager.get_subscribers(signal)
            log.info(
                "Dispatching signal to %d subscribers: %s confidence=%d",
                len(recipients),
                signal.get("signal_type", "?"),
                signal.get("confidence", 0),
            )

            # Send to each subscriber
            sent = 0
            for chat_id in recipients:
                try:
                    await bot.send_message(
                        chat_id    = chat_id,
                        text       = message,
                        parse_mode = "HTML",
                        disable_web_page_preview = True,
                    )
                    sub_manager.record_delivery(chat_id)
                    sent += 1
                    await asyncio.sleep(0.05)  # avoid Telegram rate limits

                except TelegramError as exc:
                    log.warning("Failed to send to chat_id=%d: %s", chat_id, exc)

            log.info("Signal dispatched to %d/%d Telegram subscribers", sent, len(recipients))

            # Fan out to Discord
            if discord_bot is not None:
                discord_recipients = discord_sub_manager.get_subscribers(signal)
                if discord_recipients:
                    discord_message  = format_signal_card_markdown(signal)
                    discord_delivered = await send_discord(discord_bot, discord_message, discord_recipients)
                    for channel_id in discord_delivered:
                        discord_sub_manager.record_delivery(channel_id)
                    sent += len(discord_delivered)
                    log.info("Signal dispatched to %d/%d Discord channels", len(discord_delivered), len(discord_recipients))

            # Fan out to LINE
            if line_messaging_api is not None:
                line_recipients = line_sub_manager.get_subscribers(signal)
                if line_recipients:
                    line_message   = format_signal_card_plain(signal)
                    line_delivered = await send_line(line_messaging_api, line_message, line_recipients)
                    for user_id in line_delivered:
                        line_sub_manager.record_delivery(user_id)
                    sent += len(line_delivered)
                    log.info("Signal dispatched to %d/%d LINE subscribers", len(line_delivered), len(line_recipients))

            # Log signal hash on-chain after dispatch, on the signal's origin chain
            signal_chain = signal.get("chain", "mantle")
            audit_logger = get_audit_logger(signal_chain)
            if audit_logger and sent > 0:
                try:
                    result = audit_logger.log_signal(type("S", (), {
                        "id":           signal.get("id") or 0,
                        "chain":        signal.get("chain", "mantle"),
                        "confidence":   signal.get("confidence", 0),
                        "deliver_at":   __import__("datetime").datetime.fromisoformat(
                                            signal.get("deliver_at", __import__("datetime").datetime.utcnow().isoformat())
                                        ),
                        "cluster":      type("C", (), {
                            "chain":        signal.get("chain", "mantle"),
                            "pool_address": signal.get("pool_address", ""),
                            "protocol":     type("P", (), {"value": signal.get("protocol", "")})(),
                        })(),
                        "signal_type":  type("T", (), {"value": signal.get("signal_type", "")})(),
                        "summary":      signal.get("summary", ""),
                    })())
                    if result:
                        log.info("🔐 Signal logged on-chain: tx=%s", result.tx_hash[:14])
                        stats["audit_tx_hash"] = result.tx_hash
                except Exception as exc:
                    log.warning("On-chain audit failed: %s", exc)

        except Exception as exc:
            log.warning("Dispatch error: %s", exc)
            await asyncio.sleep(2)


async def main() -> None:
    token     = os.getenv("TELEGRAM_BOT_TOKEN", "")
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    if not token:
        log.error("TELEGRAM_BOT_TOKEN not set in .env")
        return

    discord_token        = os.getenv("DISCORD_BOT_TOKEN", "")
    line_access_token     = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
    line_channel_secret   = os.getenv("LINE_CHANNEL_SECRET", "")
    line_port             = int(os.getenv("LINE_PORT", "8000"))

    log.info("=" * 50)
    log.info("  Mantis Scout — Telegram + Discord + LINE")
    log.info("  Mantle · Arbitrum · HashKey Chain signal delivery")
    log.info("=" * 50)

    # Build the Telegram application
    app = Application.builder().token(token).build()
    register_handlers(app, sub_manager, stats, redis_url)

    # Start the Telegram bot
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    log.info("Telegram bot started — polling for commands")

    background_tasks = []

    # Optionally start the Discord bot
    discord_bot = None
    if discord_token:
        from src.discord.bot import build_bot, discord_sub_manager
        discord_bot = build_bot(discord_sub_manager, stats, redis_url)
        background_tasks.append(asyncio.create_task(discord_bot.start(discord_token)))
        log.info("Discord bot starting...")
    else:
        log.info("DISCORD_BOT_TOKEN not set — Discord bot disabled")

    # Optionally start the LINE webhook server
    line_messaging_api = None
    line_api_client    = None
    if line_access_token and line_channel_secret:
        from src.line.bot import build_messaging_api, run_line_bot, line_sub_manager
        line_api_client, line_messaging_api = build_messaging_api(line_access_token)
        background_tasks.append(asyncio.create_task(
            run_line_bot(line_channel_secret, line_messaging_api, line_sub_manager, stats, redis_url, port=line_port)
        ))
        log.info("LINE bot starting on port %d...", line_port)
    else:
        log.info("LINE_CHANNEL_ACCESS_TOKEN/LINE_CHANNEL_SECRET not set — LINE bot disabled")

    # Run signal dispatcher concurrently across every enabled platform
    bot = app.bot
    dispatch_task = asyncio.create_task(
        dispatch_signals(bot, redis_url, discord_bot=discord_bot, line_messaging_api=line_messaging_api)
    )
    background_tasks.append(dispatch_task)

    try:
        # Run until interrupted
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        for task in background_tasks:
            task.cancel()
        if discord_bot is not None:
            await discord_bot.close()
        if line_api_client is not None:
            await line_api_client.close()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        log.info("Bot stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Delivery worker stopped")
