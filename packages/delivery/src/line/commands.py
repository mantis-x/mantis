"""
LINE command handling for Mantis Scout bot.

LINE has no slash-command convention, so users type plain words:
  subscribe [chain] / unsubscribe / status / history / verify <id> / help

Subscribing also happens automatically when a user adds the bot as
a friend (the "follow" webhook event).

Chain filter examples:
  subscribe              → all chains
  subscribe mantle       → Mantle only
  subscribe arbitrum     → Arbitrum only
  subscribe hashkey      → HashKey Chain only
"""
from __future__ import annotations

import logging

from src.formatters.signal_card import (
    format_status_card_plain,
    format_history_card_plain,
)
from src.common.subscription_manager import SubscriptionManager
from src.common.live_stats import get_live_candidates

log = logging.getLogger(__name__)

SUPPORTED_CHAINS = {"mantle", "arbitrum", "hashkey"}

WELCOME_MSG = """Welcome to Mantis Scout!

I monitor Mantle, Arbitrum & HashKey Chain DeFi 24/7 and alert you when smart money moves.

What I detect:
  - Smart money accumulation
  - Whale entries / exits
  - Unusual volume spikes

Chains: Mantle · Arbitrum · HashKey Chain
Every signal is hashed on-chain — fully auditable.

You're now subscribed (all chains). Send "help" to see all commands.
Free tier: 3 alerts/day · No credit card needed"""

HELP_MSG = """Mantis Scout — Commands

subscribe [chain]  Start receiving signals (chain: mantle | arbitrum | hashkey | all)
unsubscribe        Stop receiving signals
status             Bot status and stats
history            Last 5 signals
verify <id>        Verify a signal on-chain
help               This message

Chain filter examples:
  subscribe              (all chains)
  subscribe mantle       (Mantle only)
  subscribe arbitrum     (Arbitrum only)
  subscribe hashkey      (HashKey Chain only)

Free tier: 3 alerts/day
Pro tier: Unlimited alerts + Execute agent

github.com/mantis-x/mantis"""

EXPLORER_CONTRACT = "https://explorer.mantle.xyz/address/0xd745Fc0c28B8755b6280232a179e21C50B1D3adf"


def _parse_chain_arg(arg: str) -> tuple:
    """Returns (chains_set_or_None, display_text, error_or_None)."""
    arg = arg.strip().lower()
    if not arg or arg == "all":
        return None, "all chains", None
    if arg in SUPPORTED_CHAINS:
        return {arg}, arg, None
    return "invalid", arg, f'Unknown chain "{arg}". Supported: mantle, arbitrum, hashkey, or leave blank for all.'


def handle_follow(user_id: str, sub_manager: SubscriptionManager) -> str:
    sub_manager.subscribe(user_id)
    log.info("New LINE follower: user_id=%s", user_id)
    return WELCOME_MSG


def handle_unfollow(user_id: str, sub_manager: SubscriptionManager) -> None:
    sub_manager.unsubscribe(user_id)
    log.info("LINE user unfollowed: user_id=%s", user_id)


async def handle_text(text: str, user_id: str, sub_manager: SubscriptionManager, stats: dict, redis_url: str = "") -> str:
    """Return the reply text for an incoming LINE text message."""
    command, _, arg = text.strip().lower().partition(" ")

    if command in ("subscribe", "start"):
        chains, display, error = _parse_chain_arg(arg)
        if error:
            return error

        is_new = sub_manager.subscribe(user_id)
        sub_manager.set_chains(user_id, chains)

        if is_new:
            return (
                f"Subscribed! Receiving signals from {display}.\n"
                "Free tier: 3 alerts/day.\n\n"
                'Send "subscribe mantle", "subscribe arbitrum", or "subscribe hashkey" to filter by chain.\n'
                'Send "unsubscribe" to stop at any time.'
            )
        return f'Chain filter updated → {display}.'

    if command == "unsubscribe":
        removed = sub_manager.unsubscribe(user_id)
        if removed:
            return 'Unsubscribed. You won\'t receive any more signals.\nSend "subscribe" to re-subscribe.'
        return 'You weren\'t subscribed. Send "subscribe" to start.'

    if command == "status":
        stats["line_subscribers"] = sub_manager.subscriber_count()
        live_candidates = await get_live_candidates(redis_url)
        if live_candidates is not None:
            stats["candidates"] = live_candidates
        return format_status_card_plain(stats)

    if command == "history":
        return format_history_card_plain(sub_manager.get_history(5))

    if command == "verify":
        if not arg:
            return (
                'Usage: "verify <signal_id>"\n'
                'Example: "verify 42"\n\n'
                f"Check the signal's on-chain hash at:\n{EXPLORER_CONTRACT}"
            )
        return (
            f"Signal #{arg} audit\n\n"
            "Every Mantis Scout signal is hashed with keccak256 "
            "and recorded immutably on Mantle.\n\n"
            f"View SignalAuditLog on Mantle Explorer:\n{EXPLORER_CONTRACT}"
        )

    if command == "help":
        return HELP_MSG

    return 'Unrecognized command. Send "help" to see what I can do.'
