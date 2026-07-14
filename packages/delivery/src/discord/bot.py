"""
Discord delivery channel for Mantis Scout.

Run as part of the delivery worker (src/worker.py) alongside Telegram
and LINE. Requires DISCORD_BOT_TOKEN.
"""
from __future__ import annotations

import logging

import discord
from discord.ext import commands

from src.discord.commands import register_handlers, WELCOME_MSG
from src.common.subscription_manager import SubscriptionManager

log = logging.getLogger(__name__)

discord_sub_manager = SubscriptionManager(channel="discord")


def build_bot(sub_manager: SubscriptionManager, stats: dict, redis_url: str = "") -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True

    bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
    register_handlers(bot, sub_manager, stats, redis_url)

    @bot.event
    async def on_guild_join(guild) -> None:
        if guild.system_channel:
            await guild.system_channel.send(WELCOME_MSG)

    return bot


async def run_discord_bot(token: str, sub_manager: SubscriptionManager, stats: dict, redis_url: str = "") -> None:
    bot = build_bot(sub_manager, stats, redis_url)
    try:
        await bot.start(token)
    except Exception as exc:
        log.warning("Discord bot stopped: %s", exc)


async def send_signal_to_subscribers(bot: commands.Bot, message: str, channel_ids: list[str]) -> list[str]:
    """Send a formatted signal message to a list of Discord channel ids. Returns ids that succeeded."""
    delivered = []
    for channel_id in channel_ids:
        try:
            channel = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
            await channel.send(message)
            delivered.append(channel_id)
        except Exception as exc:
            log.warning("Failed to send to Discord channel_id=%s: %s", channel_id, exc)
    return delivered
