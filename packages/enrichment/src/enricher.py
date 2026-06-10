"""
Enricher: calls Claude Sonnet with a cluster context prompt and parses
the structured JSON response into a Signal object.
"""
from __future__ import annotations
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import anthropic

from src.prompts.signal_classifier import SYSTEM_PROMPT, build_prompt
from src.parsers.llm_response import parse_llm_response

log = logging.getLogger(__name__)

HOLD_MINUTES = int(os.getenv("SIGNAL_HOLD_MINUTES", "5"))
MIN_CONFIDENCE = int(os.getenv("MIN_CONFIDENCE_SCORE", "50"))

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


class Enricher:
    def enrich(self, cluster, wallet_details: str, sentiment: str):
        """
        Enrich a WalletCluster into a Signal via LLM classification.
        Returns None if confidence < MIN_CONFIDENCE.
        """
        prompt = build_prompt(cluster, wallet_details, sentiment)

        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=512,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text
        except Exception as exc:
            log.error("Claude API error: %s", exc)
            return None

        signal_data = parse_llm_response(raw)
        if signal_data is None:
            return None

        if signal_data["confidence"] < MIN_CONFIDENCE:
            log.debug(
                "Signal discarded: confidence=%d < threshold=%d",
                signal_data["confidence"], MIN_CONFIDENCE,
            )
            return None

        deliver_at = datetime.now(tz=timezone.utc) + timedelta(minutes=HOLD_MINUTES)

        return {
            "cluster": cluster,
            "signal_type": signal_data["signal_type"],
            "confidence": signal_data["confidence"],
            "summary": signal_data["summary"],
            "key_factors": signal_data["key_factors"],
            "deliver_at": deliver_at,
        }
