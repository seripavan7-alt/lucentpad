"""Background span exporter: a daemon thread that batches finished spans to ``POST /v1/spans``.

The host only ever appends to a bounded deque under a lock; all network I/O happens on the
exporter thread. When the buffer is full the oldest span is dropped and counted. Failed sends
(network errors, 429, 5xx) are put back at the front of the buffer (still bounded) and retried
after a backoff (``Retry-After`` on 429). The request carries no credentials: only the JSON body.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from collections import deque
from typing import Any

import httpx

from ._attrs import MAX_BATCH_SPANS

log = logging.getLogger("lucentpad")

SpanDict = dict[str, Any]

DEFAULT_MAX_BUFFER = 10_000
DEFAULT_BATCH_SIZE = 100
DEFAULT_INTERVAL = 0.25
DEFAULT_TIMEOUT = 2.0
DEFAULT_MAX_BACKOFF = 30.0


class Exporter:
    def __init__(
        self,
        endpoint: str,
        *,
        max_buffer: int = DEFAULT_MAX_BUFFER,
        batch_size: int = DEFAULT_BATCH_SIZE,
        interval: float = DEFAULT_INTERVAL,
        timeout: float = DEFAULT_TIMEOUT,
        max_backoff: float = DEFAULT_MAX_BACKOFF,
        transport: httpx.BaseTransport | None = None,
        start: bool = True,
    ) -> None:
        self.url = endpoint.rstrip("/") + "/v1/spans"
        self._max_buffer = max(1, max_buffer)
        self._batch_size = max(1, min(batch_size, MAX_BATCH_SPANS))
        self._interval = interval
        self._max_backoff = max_backoff
        self._buf: deque[SpanDict] = deque()
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._inflight = 0
        self._closed = False
        self._flush_requested = False
        self._backoff_until = 0.0
        self._consecutive_failures = 0
        self._warned_drop = False
        # Counters (read by tests and, later, by a stats hook).
        self.dropped = 0  # oldest spans evicted because the buffer was full
        self.rejected = 0  # spans the server refused for good (4xx other than 408/429)
        self.sent = 0
        self.failures = 0  # failed send attempts (each is retried)
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 1.0)),
            transport=transport,
            headers={"content-type": "application/json", "user-agent": "lucentpad-sdk"},
            follow_redirects=False,
        )
        self._thread = threading.Thread(target=self._run, name="lucentpad-exporter", daemon=True)
        if start:
            self._thread.start()

    # ------------------------------------------------------------------ host side (never blocks)

    def submit(self, span: SpanDict) -> None:
        with self._lock:
            if self._closed:
                self.dropped += 1
                return
            if len(self._buf) >= self._max_buffer:
                self._buf.popleft()
                self.dropped += 1
                warn = not self._warned_drop
                self._warned_drop = True
            else:
                warn = False
            self._buf.append(span)
            full_batch = len(self._buf) >= self._batch_size
        if warn:
            log.warning("lucentpad: span buffer full; dropping the oldest spans")
        if full_batch:
            self._wake.set()

    @property
    def buffered(self) -> int:
        with self._lock:
            return len(self._buf)

    def flush(self, timeout: float = 2.0) -> bool:
        """Ask the exporter thread to send everything now; wait at most ``timeout`` seconds."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._lock:
            if not self._buf and not self._inflight:
                return True
            if not self._thread.is_alive():
                return False
            failures_before = self.failures
            rejected_before = self.rejected
            self._flush_requested = True
        self._wake.set()
        with self._changed:
            while self._buf or self._inflight:
                if self.failures > failures_before:
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._changed.wait(remaining)
            return self.rejected == rejected_before

    def shutdown(self, timeout: float = 2.0) -> bool:
        ok = self.flush(timeout)
        with self._lock:
            self._closed = True
        self._stop.set()
        self._wake.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(0.5)
        try:
            self._client.close()
        except Exception:  # noqa: S110 - closing is best effort
            pass
        return ok

    # ------------------------------------------------------------------ exporter thread

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self._interval)
            self._wake.clear()
            try:
                self._drain()
            except Exception:  # never let the thread die
                log.debug("lucentpad: exporter loop error", exc_info=True)

    def _drain(self) -> None:
        while True:
            with self._lock:
                if not self._buf:
                    self._flush_requested = False
                    self._changed.notify_all()
                    return
                if time.monotonic() < self._backoff_until and not self._flush_requested:
                    return
                n = min(self._batch_size, len(self._buf))
                batch = [self._buf.popleft() for _ in range(n)]
                self._inflight = n
            retry = self._send(batch)
            with self._lock:
                self._inflight = 0
                if retry:
                    self._requeue(retry)
                    self.failures += 1
                    self._consecutive_failures += 1
                    self._flush_requested = False
                    self._changed.notify_all()
                    return
                self._consecutive_failures = 0
                self._changed.notify_all()

    def _requeue(self, spans: list[SpanDict]) -> None:
        """Put failed spans back at the front, dropping the oldest if full (lock held)."""
        room = self._max_buffer - len(self._buf)
        if room < len(spans):
            self.dropped += len(spans) - max(room, 0)
            spans = spans[len(spans) - max(room, 0) :]
        self._buf.extendleft(reversed(spans))

    def _set_backoff(self, retry_after: float | None) -> None:
        if retry_after is None:
            base = min(self._max_backoff, 0.5 * 2 ** min(self._consecutive_failures, 10))
            retry_after = base * (0.5 + random.random() / 2)  # noqa: S311 - jitter, not crypto
        self._backoff_until = time.monotonic() + min(max(retry_after, 0.0), self._max_backoff)

    def _send(self, batch: list[SpanDict]) -> list[SpanDict]:
        """POST one batch; return the spans to retry (empty when done or dropped for good)."""
        try:
            body = json.dumps({"spans": batch}, default=str, separators=(",", ":")).encode()
            resp = self._client.post(self.url, content=body)
        except Exception:
            log.debug("lucentpad: export failed (network)")
            self._set_backoff(None)
            return batch
        code = resp.status_code
        if 200 <= code < 300:
            self.sent += len(batch)
            return []
        if code == 429:
            self._set_backoff(_retry_after(resp.headers.get("retry-after")))
            return batch
        if code == 408 or code >= 500:
            self._set_backoff(None)
            return batch
        if code == 413 and len(batch) > 1:
            half = len(batch) // 2
            return self._send(batch[:half]) + self._send(batch[half:])
        log.debug("lucentpad: ingest refused %d spans (HTTP %d)", len(batch), code)
        self.rejected += len(batch)
        return []


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None  # HTTP-date form: fall back to exponential backoff
