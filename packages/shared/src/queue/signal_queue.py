"""
SignalQueue: delayed Redis queue shared by delivery and executor consumers.

Producer (enrichment worker) calls push(signal).
Consumers (delivery, executor) call pop() in a loop — returns None if
no signal is ready (deliver_at in the future) or queue is empty.

The 5-minute delay is enforced here: signals sit in a Redis sorted set
keyed by their deliver_at Unix timestamp. Consumers only receive signals
whose deliver_at <= now.
"""
from __future__ import annotations
import json
import time
from dataclasses import asdict
from typing import Optional

import redis

from src.schemas.signal import Signal


QUEUE_KEY = "signal_queue"   # Redis sorted set key


class SignalQueue:
    def __init__(self, redis_url: str):
        self._r = redis.from_url(redis_url, decode_responses=True)

    def push(self, signal: Signal) -> None:
        """Enqueue a signal. Score = deliver_at Unix timestamp."""
        payload = json.dumps(asdict(signal), default=str)
        score   = signal.deliver_at.timestamp()
        self._r.zadd(QUEUE_KEY, {payload: score})

    def pop(self) -> Optional[dict]:
        """
        Return the next ready signal (deliver_at <= now) and remove it,
        or None if nothing is ready.
        """
        now     = time.time()
        results = self._r.zrangebyscore(QUEUE_KEY, "-inf", now, start=0, num=1)
        if not results:
            return None
        payload = results[0]
        removed = self._r.zrem(QUEUE_KEY, payload)
        if removed:
            return json.loads(payload)
        return None  # another consumer grabbed it first

    def depth(self) -> int:
        """Total signals in queue (including future ones)."""
        return self._r.zcard(QUEUE_KEY)

    def pending(self) -> int:
        """Signals not yet ready for delivery."""
        return self._r.zcount(QUEUE_KEY, time.time(), "+inf")
