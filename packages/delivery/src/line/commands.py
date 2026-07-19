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
  subscribe ethereum     → Ethereum only
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

WELCOME_MSG = """Welcome to Mantis Scout!

I monitor Mantle, Arbitrum, HashKey Chain & Ethereum DeFi 24/7 and alert you when smart money moves.

What I detect:
  - Smart money accumulation
  - Whale entries / exits
  - Unusual volume spikes

Chains: Mantle · Arbitrum · HashKey Chain · Ethereum
Every signal is hashed on-chain — fully auditable.

You're now subscribed (all chains). Send "help" to see all commands.
Free tier: 3 alerts/day · No credit card needed"""

HELP_MSG = """Mantis Scout — Commands

subscribe [chain]  Start receiving signals (chain: mantle | arbitrum | hashkey | ethereum | all)
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
  subscribe ethereum     (Ethereum only)

Free tier: 3 alerts/day
Pro tier: Unlimited alerts + Execute agent

github.com/mantis-x/mantis"""

def _parse_chain_arg(arg: str) -> tuple:
    """Returns (chains_set_or_None, display_text, error_or_None)."""
    arg = arg.strip().lower()
    if not arg or arg == "all":
        return None, "all chains", None
    if arg in SUPPORTED_CHAINS:
        return {arg}, arg, None
    return "invalid", arg, f'Unknown chain "{arg}". Supported: mantle, arbitrum, hashkey, ethereum, or leave blank for all.'


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
                'Send "subscribe mantle", "subscribe arbitrum", "subscribe hashkey", or "subscribe ethereum" to filter by chain.\n'
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
        enrichment_errors = await get_enrichment_consecutive_errors(redis_url)
        if enrichment_errors is not None:
            stats["enrichment_errors"] = enrichment_errors
        return format_status_card_plain(stats)

    if command == "history":
        return format_history_card_plain(sub_manager.get_history(5))

    if command == "verify":
        links = "\n".join(
            f"{_CHAIN_LABEL[c]}: {get_explorer_contract_url(c)}"
            for c in ("mantle", "arbitrum", "hashkey", "ethereum")
            if get_explorer_contract_url(c)
        )

        if not arg:
            return (
                'Usage: "verify <signal_id>"\n'
                'Example: "verify 42"\n\n'
                "Find the ID on the alert itself (Signal #...) or with \"history\". "
                "Every signal is hashed on its origin chain — give an ID and I'll "
                f"look up the right one, or browse SignalAuditLog directly:\n{links}"
            )

        signal = sub_manager.get_signal_by_id(arg)
        if signal is None:
            return (
                f"Signal #{arg}\n\n"
                "I don't have this one in recent history (older than the last 50 "
                "signals, or an invalid ID) — so I can't tell you which chain it's "
                f"logged on. You can still browse SignalAuditLog directly:\n{links}"
            )

        chain    = signal.get("chain", "mantle")
        label    = _CHAIN_LABEL.get(chain, chain.capitalize())
        explorer = get_explorer_contract_url(chain)
        return (
            f"Signal #{arg} audit\n\n"
            "Every Mantis Scout signal is hashed with keccak256 "
            f"and recorded immutably — this one on {label}.\n\n"
            f"View SignalAuditLog on {label} Explorer:\n{explorer}"
        )

    if command == "help":
        return HELP_MSG

    return 'Unrecognized command. Send "help" to see what I can do.'
