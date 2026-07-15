"""
Enricher — calls Claude Sonnet with anomaly candidate context
and returns a fully classified Signal.

The enricher is stateless — create one instance and call enrich()
for each candidate. Rate limiting and retry logic is handled here.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import anthropic

from src.prompts.signal_classifier import SYSTEM_PROMPT, build_prompt
from src.parsers.llm_response import parse_llm_response
from src.models.signal import Signal, SignalType

log = logging.getLogger(__name__)

MIN_CONFIDENCE  = int(os.getenv("MIN_CONFIDENCE_SCORE", "50"))
HOLD_MINUTES    = int(os.getenv("SIGNAL_HOLD_MINUTES",  "5"))
MAX_RETRIES     = 2
RETRY_DELAY     = 2.0   # seconds


class Enricher:
    """
    Enriches AnomalyCandidate dicts into Signal objects
    using Claude Sonnet as the classifier.
    """

    def __init__(self, api_key: str = ""):
        api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")
        self._client    = anthropic.Anthropic(api_key=api_key)
        self._enriched  = 0
        self._discarded = 0
        self._errors    = 0

    def enrich(self, candidate: dict) -> Optional[Signal]:
        """
        Enrich one AnomalyCandidate dict into a Signal.
        Returns None if confidence < MIN_CONFIDENCE or Claude call fails.
        """
        prompt = build_prompt(candidate)
        raw    = self._call_claude(prompt)

        if raw is None:
            self._errors += 1
            return None

        parsed = parse_llm_response(raw)
        if parsed is None:
            self._errors += 1
            return None

        if parsed["confidence"] < MIN_CONFIDENCE:
            log.debug(
                "Signal discarded: confidence=%d < %d",
                parsed["confidence"], MIN_CONFIDENCE
            )
            self._discarded += 1
            return None

        now        = datetime.now(tz=timezone.utc)
        deliver_at = now + timedelta(minutes=HOLD_MINUTES)

        signal = Signal(
            id              = None,
            chain           = candidate.get("chain", "mantle"),
            protocol        = candidate.get("protocol", "unknown"),
            pool_address    = candidate.get("pool_address", ""),
            wallets         = candidate.get("wallets", []),
            signal_type     = SignalType(parsed["signal_type"]),
            confidence      = parsed["confidence"],
            summary         = parsed["summary"],
            key_factors     = parsed["key_factors"],
            detected_at     = now,
            deliver_at      = deliver_at,
            z_score         = float(candidate.get("z_score", 0)),
            total_volume_usd= float(candidate.get("total_volume_usd", 0)),
            event_type      = candidate.get("event_type", "swap"),
        )

        self._enriched += 1
        log.info(
            "✅ Signal enriched: type=%s confidence=%d z=%.2f vol=$%.0f",
            signal.signal_type.value, signal.confidence,
            signal.z_score, signal.total_volume_usd,
        )
        return signal

    def _call_claude(self, prompt: str) -> Optional[str]:
        """Call Claude Sonnet with retry logic."""
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self._client.messages.create(
                    model      = "claude-sonnet-5",
                    max_tokens = 512,
                    # This is a small JSON classification call with no need for
                    # multi-step reasoning -- disable thinking explicitly rather
                    # than relying on the default. Sonnet 5 runs adaptive
                    # thinking when `thinking` is omitted (unlike the retired
                    # Sonnet 4 model this was originally written against), which
                    # prepends a ThinkingBlock to response.content ahead of the
                    # TextBlock. content[0].text was crashing on every call once
                    # migrated to Sonnet 5, since content[0] was the
                    # ThinkingBlock, not the TextBlock.
                    thinking   = {"type": "disabled"},
                    system     = SYSTEM_PROMPT,
                    messages   = [{"role": "user", "content": prompt}],
                )
                for block in response.content:
                    if block.type == "text":
                        return block.text
                log.error("Claude response had no text block: %r", response.content)
                return None

            except anthropic.RateLimitError:
                if attempt < MAX_RETRIES:
                    log.warning("Rate limited — waiting %.1fs", RETRY_DELAY)
                    time.sleep(RETRY_DELAY * (attempt + 1))
                else:
                    log.error("Rate limit exceeded after %d retries", MAX_RETRIES)
                    return None

            except anthropic.APIError as exc:
                log.error("Claude API error: %s", exc)
                return None

            except Exception as exc:
                log.error("Unexpected error calling Claude: %s", exc)
                return None

        return None

    @property
    def stats(self) -> dict:
        total = self._enriched + self._discarded + self._errors
        return {
            "enriched":  self._enriched,
            "discarded": self._discarded,
            "errors":    self._errors,
            "total":     total,
        }
