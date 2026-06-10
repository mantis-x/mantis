"""
EventNormaliser: converts raw Web3 log dicts into the shared NormalisedEvent
schema understood by the detection package.

Each decoder (merchant_moe, agni_finance, fluxion) registers itself here.
The normaliser inspects the log topic and routes to the correct decoder.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class NormalisedEvent:
    block_number:  int
    tx_hash:       str
    protocol:      str           # "merchant_moe" | "agni_finance" | "fluxion"
    pool_address:  str
    wallet_address: str
    event_type:    str           # "swap" | "mint" | "burn"
    amount_usd:    float
    token_in:      Optional[str]
    token_out:     Optional[str]
    timestamp:     datetime


class EventNormaliser:
    """Routes raw logs to the correct protocol decoder."""

    _decoders: dict = {}

    @classmethod
    def register(cls, protocol: str, decoder) -> None:
        cls._decoders[protocol] = decoder

    @classmethod
    def normalise(cls, raw_log: dict, block_timestamp: int) -> Optional[NormalisedEvent]:
        for protocol, decoder in cls._decoders.items():
            if decoder.can_handle(raw_log):
                try:
                    return decoder.decode(raw_log, protocol, block_timestamp)
                except Exception as exc:
                    log.warning("Decode error [%s]: %s", protocol, exc)
                    return None
        log.debug("No decoder for log: %s", raw_log.get("address"))
        return None
