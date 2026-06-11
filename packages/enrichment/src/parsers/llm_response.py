"""
LLM response parser — safely extracts structured data from Claude's output.

Claude is instructed to return JSON only, but we defensively
strip any accidental markdown fences before parsing.
"""
from __future__ import annotations
import json
import logging
import re
from typing import Optional

log = logging.getLogger(__name__)

VALID_SIGNAL_TYPES = {
    "accumulation", "distribution",
    "whale_entry", "whale_exit", "unusual_volume"
}


def parse_llm_response(raw: str) -> Optional[dict]:
    """
    Parse Claude's JSON response into a structured dict.
    Returns None if parsing fails or output is invalid.

    Expected output:
    {
        "summary":     str,
        "confidence":  int (0-100),
        "signal_type": str,
        "key_factors": list[str]
    }
    """
    if not raw or not raw.strip():
        log.warning("Empty LLM response")
        return None

    # Strip markdown fences if present
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        log.warning("JSON parse failed: %s | raw=%s", exc, raw[:200])
        return None

    # Validate required fields
    if not isinstance(data, dict):
        log.warning("LLM returned non-dict: %s", type(data))
        return None

    # summary
    summary = data.get("summary", "").strip()
    if not summary:
        log.warning("Missing summary in LLM response")
        return None

    # confidence
    try:
        confidence = int(data.get("confidence", 0))
        confidence = max(0, min(100, confidence))
    except (TypeError, ValueError):
        log.warning("Invalid confidence value: %s", data.get("confidence"))
        return None

    # signal_type
    signal_type = str(data.get("signal_type", "")).strip().lower()
    if signal_type not in VALID_SIGNAL_TYPES:
        log.warning("Unknown signal_type: %s — defaulting to unusual_volume", signal_type)
        signal_type = "unusual_volume"

    # key_factors
    raw_factors = data.get("key_factors", [])
    if isinstance(raw_factors, list):
        key_factors = [str(f).strip() for f in raw_factors if f][:5]
    else:
        key_factors = [str(raw_factors)]

    if not key_factors:
        key_factors = ["Unusual on-chain activity detected"]

    return {
        "summary":     summary,
        "confidence":  confidence,
        "signal_type": signal_type,
        "key_factors": key_factors,
    }
