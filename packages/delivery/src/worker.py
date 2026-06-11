"""
Delivery Worker — entry point for Mantis Scout Telegram bot.
Run: python -m src.worker (from packages/delivery/)

Runs two concurrent tasks:
  1. Telegram bot — handles user commands
  2. Signal dispatcher — polls Redis mantis:signals and pushes to subscribers

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
from src.formatters.signal_card import format_signal_card

# Shared state
sub_manager = SubscriptionManager()
stats       = {
    "signals_today": 0,
    "candidates":    0,
    "pools":         11,
    "subscribers":   0,
    "started_at":    time.time(),
}


async def dispatch_signals(bot: Bot, redis_url: str) -> None:
    """
    Continuously poll Redis for new signals and push to subscribers.
    Runs as a background task alongside the Telegram bot.
    """
    import redis.asyncio as aioredis
    r = aioredis.from_url(redis_url, decode_responses=True)

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

            log.info("Signal dispatched to %d/%d subscribers", sent, len(recipients))

        except Exception as exc:
            log.warning("Dispatch error: %s", exc)
            await asyncio.sleep(2)


async def main() -> None:
    token     = os.getenv("TELEGRAM_BOT_TOKEN", "")
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    if not token:
        log.error("TELEGRAM_BOT_TOKEN not set in .env")
        return

    log.info("=" * 50)
    log.info("  Mantis Scout — Telegram Bot")
    log.info("  Mantle DeFi signal delivery")
    log.info("=" * 50)

    # Build the Telegram application
    app = Application.builder().token(token).build()
    register_handlers(app, sub_manager, stats)

    # Start the bot
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    log.info("Bot started — polling for commands")

    # Run signal dispatcher concurrently
    bot = app.bot
    dispatch_task = asyncio.create_task(
        dispatch_signals(bot, redis_url)
    )

    try:
        # Run until interrupted
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        dispatch_task.cancel()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        log.info("Bot stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Delivery worker stopped")
