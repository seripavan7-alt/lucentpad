"""Background writer: drains the ingest queue into ``SpanStore.insert_spans`` in batches.

``QueuedIngest`` is the app's ``IngestPipeline``: the route offers batches to the queue and
returns at once; one writer task takes up to ``batch_max`` spans or whatever arrived within
``batch_wait`` seconds and stores them in one insert. A failed insert is retried with
exponential backoff, then the batch is counted in ``write_errors_total`` and dropped. The
task never dies on an error. ``stop`` refuses new spans and drains the queue within a timeout.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Sequence

from lucentpad_server.ingest.queue import SpanQueue
from lucentpad_server.schema import IngestStats, Span
from lucentpad_server.store import SpanStore

log = logging.getLogger(__name__)

DRAIN_TIMEOUT_ENV = "LUCENTPAD_INGEST_DRAIN_TIMEOUT"
DEFAULT_DRAIN_TIMEOUT = 5.0


def drain_timeout_from_env() -> float:
    """``$LUCENTPAD_INGEST_DRAIN_TIMEOUT`` (seconds to flush the queue on shutdown), default 5."""
    raw = os.environ.get(DRAIN_TIMEOUT_ENV, "").strip()
    return float(raw) if raw else DEFAULT_DRAIN_TIMEOUT


class QueuedIngest:
    """``IngestPipeline`` over a ``SpanQueue`` and one background writer task."""

    def __init__(
        self,
        store: SpanStore,
        queue: SpanQueue,
        *,
        batch_max: int = 1000,
        batch_wait: float = 0.1,
        retries: int = 4,
        backoff: float = 0.2,
        backoff_max: float = 2.0,
    ) -> None:
        self._store = store
        self._queue = queue
        self._batch_max = batch_max
        self._batch_wait = batch_wait
        self._retries = retries
        self._backoff = backoff
        self._backoff_max = backoff_max
        self._task: asyncio.Task[None] | None = None
        self._closing = False
        self._in_flight = 0
        self.accepted_total = 0
        self.rejected_total = 0
        self.written_total = 0
        self.write_errors_total = 0
        self.insert_calls = 0
        """Successful ``insert_spans`` calls (how well the writer batches)."""

    # ----------------------------------------------------------------- IngestPipeline

    def offer(self, spans: Sequence[Span]) -> bool:
        if self._closing or not self._queue.offer(spans):
            self.rejected_total += len(spans)
            return False
        self.accepted_total += len(spans)
        return True

    def stats(self) -> IngestStats:
        return IngestStats(
            queue_depth=self._queue.depth + self._in_flight,
            queue_capacity=self._queue.capacity,
            accepted_total=self.accepted_total,
            rejected_total=self.rejected_total,
            written_total=self.written_total,
            write_errors_total=self.write_errors_total,
        )

    # ----------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="lucentpad-ingest-writer")

    async def stop(self, drain_timeout: float = DEFAULT_DRAIN_TIMEOUT) -> None:
        """Stop accepting, then give the writer ``drain_timeout`` seconds to empty the queue."""
        self._closing = True
        task, self._task = self._task, None
        if task is None:
            return
        try:
            await asyncio.wait_for(task, drain_timeout)
        except TimeoutError:
            lost = self._queue.depth + self._in_flight
            log.error(
                "ingest drain timed out after %.1fs; %d spans not written", drain_timeout, lost
            )

    # ----------------------------------------------------------------- writer

    async def _run(self) -> None:
        while not (self._closing and self._queue.depth == 0):
            try:
                batch = await self._queue.drain(self._batch_max, self._batch_wait)
                if batch:
                    self._in_flight = len(batch)
                    await self._write(batch)
            except asyncio.CancelledError:
                raise
            except Exception:  # never let the writer die
                log.exception("ingest writer error")
            finally:
                self._in_flight = 0

    async def _write(self, batch: list[Span]) -> None:
        for attempt in range(self._retries + 1):
            try:
                await self._store.insert_spans(batch)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning(
                    "ingest write of %d spans failed (attempt %d/%d): %s",
                    len(batch),
                    attempt + 1,
                    self._retries + 1,
                    type(exc).__name__,
                )
                if attempt < self._retries:
                    await asyncio.sleep(min(self._backoff * 2**attempt, self._backoff_max))
                continue
            self.written_total += len(batch)
            self.insert_calls += 1
            return
        self.write_errors_total += len(batch)
        log.error("dropped %d spans after %d failed writes", len(batch), self._retries + 1)
