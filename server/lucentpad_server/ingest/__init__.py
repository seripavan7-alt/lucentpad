"""Ingest pipeline: ``POST /v1/spans`` offers batches to a bounded queue; a background writer
drains it into ``SpanStore.insert_spans``. The route never waits for the database.

``IngestPipeline`` is the seam the routes depend on (part of the API contract). The app's
lifespan puts an implementation on ``app.state.ingest``; without one, ingest answers 501.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from lucentpad_server.schema import IngestStats, Span


class IngestPipeline(Protocol):
    def offer(self, spans: Sequence[Span]) -> bool:
        """Queue the whole batch, or nothing (False) when it doesn't fit. Never blocks."""
        ...

    def stats(self) -> IngestStats: ...
