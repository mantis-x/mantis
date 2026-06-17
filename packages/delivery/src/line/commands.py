"""
LINE command handling for Mantis Scout bot.

LINE has no slash-command convention, so users type plain words:
  subscribe / unsubscribe / status / history / verify <id> / help

Subscribing also happens automatically when a user adds the bot as
a friend (the "follow" webhook event).
"""
from __future__ import annotations

import logging

from src.formatters.signal_card import (
    format_status_card_plain,
    format_history_card_plain,
)
from src.common.subscription_manager import SubscriptionManager

log = logging.getLogger(__name__)

WELCOME_MSG = """Welcome to Mantis Scout!

I monitor Mantle DeFi 24/7 and alert you when smart money moves.

What I detect:
  - Smart money accumulation
  - Whale entries / exits
  - Unusual volume spikes

Every signal is hashed on Mantle — fully auditable.

You're now subscribed. Send "help" to see all commands.
Free tier: 3 alerts/day · No credit card needed"""

HELP_MSG = """Mantis Scout — Commands

subscribe    Start receiving signals
unsubscribe  Stop receiving signals
status       Bot status and stats
history      Last 5 signals
verify <id>  Verify a signal on-chain
help         This message

Free tier: 3 alerts/day
Pro tier: Unlimited alerts + Execute agent

github.com/mantis-x/mantis"""

EXPLORER_CONTRACT = "https://explorer.mantle.xyz/address/0xd745Fc0c28B8755b6280232a179e21C50B1D3adf"


def handle_follow(user_id: str, sub_manager: SubscriptionManager) -> str:
    sub_manager.subscribe(user_id)
    log.info("New LINE follower: user_id=%s", user_id)
    return WELCOME_MSG


def handle_unfollow(user_id: str, sub_manager: SubscriptionManager) -> None:
    sub_manager.unsubscribe(user_id)
    log.info("LINE user unfollowed: user_id=%s", user_id)


def handle_text(text: str, user_id: str, sub_manager: SubscriptionManager, stats: dict) -> str:
    """Return the reply text for an incoming LINE text message."""
    command, _, arg = text.strip().lower().partition(" ")

    if command in ("subscribe", "start"):
        is_new = sub_manager.subscribe(user_id)
        if is_new:
            return (
                "Subscribed! You'll receive Mantis Scout signals as they're detected.\n"
                "Free tier: 3 alerts/day.\n\n"
                'Send "unsubscribe" to stop at any time.'
            )
        return 'You\'re already subscribed. Send "unsubscribe" to stop.'

    if command == "unsubscribe":
        removed = sub_manager.unsubscribe(user_id)
        if removed:
            return 'Unsubscribed. You won\'t receive any more signals.\nSend "subscribe" to re-subscribe.'
        return 'You weren\'t subscribed. Send "subscribe" to start.'

    if command == "status":
        stats["line_subscribers"] = sub_manager.subscriber_count()
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
