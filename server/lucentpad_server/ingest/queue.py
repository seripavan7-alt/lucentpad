"""The ingest queue: accepted spans wait here until the writer stores them.

``SpanQueue`` is the seam where an external queue (Redis, Kafka) slots in later;
``InProcessSpanQueue`` keeps spans in memory, bounded by span count.
"""

from __future__ import annotations

import asyncio
import os
from collections import deque
from collections.abc import Sequence
from typing import Protocol

from lucentpad_server.schema import Span

DEFAULT_QUEUE_MAX = 50_000
QUEUE_MAX_ENV = "LUCENTPAD_INGEST_QUEUE_MAX"


def queue_max_from_env() -> int:
    """``$LUCENTPAD_INGEST_QUEUE_MAX`` (spans), default 50 000."""
    raw = os.environ.get(QUEUE_MAX_ENV, "").strip()
    if not raw:
        return DEFAULT_QUEUE_MAX
    value = int(raw)
    if value < 1:
        raise ValueError(f"{QUEUE_MAX_ENV} must be at least 1")
    return value


class SpanQueue(Protocol):
    @property
    def depth(self) -> int:
        """Spans waiting."""
        ...

    @property
    def capacity(self) -> int: ...

    def offer(self, spans: Sequence[Span]) -> bool:
        """Enqueue all of ``spans``, or none of them (False) when they don't fit. Never blocks."""
        ...

    async def drain(self, max_n: int, max_wait: float) -> list[Span]:
        """Take up to ``max_n`` spans, waiting at most ``max_wait`` seconds for that many to
        arrive; returns what is there (possibly nothing) when the time is up."""
        ...


class InProcessSpanQueue:
    """In-memory ``SpanQueue`` for one API process, bounded by span count."""

    def __init__(self, capacity: int = DEFAULT_QUEUE_MAX) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._capacity = capacity
        self._items: deque[Span] = deque()
        self._grew = asyncio.Event()

    @property
    def depth(self) -> int:
        return len(self._items)

    @property
    def capacity(self) -> int:
        return self._capacity

    def offer(self, spans: Sequence[Span]) -> bool:
        if len(self._items) + len(spans) > self._capacity:
            return False
        self._items.extend(spans)
        self._grew.set()
        return True

    async def drain(self, max_n: int, max_wait: float) -> list[Span]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max_wait
        while len(self._items) < max_n:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            self._grew.clear()
            try:
                await asyncio.wait_for(self._grew.wait(), remaining)
            except TimeoutError:
                break
        n = min(max_n, len(self._items))
        return [self._items.popleft() for _ in range(n)]
