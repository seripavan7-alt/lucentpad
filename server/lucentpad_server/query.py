"""Trace query parameters shared by the routes and the store (part of the API contract).

``GET /v1/traces`` and ``GET /v1/traces/facets`` take the same filters: a time window on the
trace's ``start_time`` (``from`` inclusive, ``to`` exclusive) and multi-value filters that OR
within one filter and AND across filters. The client computes the window (``from = now -
range``); the server has no range presets.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime

from lucentpad_server.schema import SpanSource, SpanStatus

FACETS = ("name", "status", "source", "client", "model", "service")
"""Facet names, in display order. Each is also a filter on ``TraceFilter``."""


@dataclass(frozen=True)
class TraceFilter:
    """Which traces a list or facet query covers. Empty tuples mean "no filter"."""

    start: datetime | None = None  # `from`, inclusive
    end: datetime | None = None  # `to`, exclusive
    name: tuple[str, ...] = field(default=())
    status: tuple[SpanStatus, ...] = field(default=())
    source: tuple[SpanSource, ...] = field(default=())
    client: tuple[str, ...] = field(default=())
    model: tuple[str, ...] = field(default=())  # matches any of the trace's models
    service: tuple[str, ...] = field(default=())

    def values(self, facet: str) -> tuple[str, ...]:
        """The selected values of one facet filter."""
        if facet not in FACETS:
            raise KeyError(facet)
        selected: tuple[str, ...] = getattr(self, facet)
        return selected

    def fingerprint(self) -> str:
        """Short stable hash of the filter set. Cursors embed it, so a cursor from one filter
        set is rejected (422) when reused with another."""
        data = {
            "from": self.start.isoformat() if self.start else None,
            "to": self.end.isoformat() if self.end else None,
            **{f: sorted(self.values(f)) for f in FACETS},
        }
        digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        return digest[:16]
