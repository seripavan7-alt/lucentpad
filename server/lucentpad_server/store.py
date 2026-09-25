"""Repository layer: write spans, read trace summaries and trace detail.

Writes go through a per-connection temp staging table filled with binary ``COPY``; a single
statement then moves new rows into ``spans`` (skipping duplicates) and folds exactly those
new rows into the pre-aggregated ``traces`` table. Re-sent spans are therefore idempotent
and never double-count tokens or cost.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import asyncpg

from lucentpad_server.pricing import cost_usd
from lucentpad_server.schema import (
    Attr,
    Attributes,
    Span,
    SpanSource,
    SpanStatus,
    TraceDetail,
    TraceSummary,
)

_SPAN_COLUMNS = (
    "trace_id",
    "span_id",
    "parent_span_id",
    "name",
    "kind",
    "source",
    "start_time",
    "end_time",
    "status",
    "status_message",
    "model",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "attributes",
    "events",
)

_CREATE_STAGE = """
CREATE TEMP TABLE IF NOT EXISTS spans_stage (LIKE spans INCLUDING DEFAULTS) ON COMMIT DELETE ROWS
"""

# Root-derived fields prefer the root span (parent_span_id IS NULL), falling back to the
# earliest span until the root arrives. Aggregates only cover rows actually inserted.
_MERGE_STAGE = """
WITH ins AS (
    INSERT INTO spans SELECT * FROM spans_stage
    ON CONFLICT (trace_id, span_id) DO NOTHING
    RETURNING *
), agg AS (
    SELECT
        trace_id,
        bool_or(parent_span_id IS NULL) AS has_root,
        (array_agg(name ORDER BY parent_span_id IS NULL DESC, start_time))[1] AS name,
        (array_agg(source ORDER BY parent_span_id IS NULL DESC, start_time))[1] AS source,
        (array_agg(attributes ->> 'service.name' ORDER BY parent_span_id IS NULL DESC,
                   start_time))[1] AS service_name,
        (array_agg(attributes ->> 'lucentpad.client' ORDER BY parent_span_id IS NULL DESC,
                   start_time))[1] AS client,
        min(start_time) AS start_time,
        max(end_time) AS end_time,
        (array_agg(status ORDER BY lucentpad_status_rank(status) DESC))[1] AS status,
        count(*) AS span_count,
        count(*) FILTER (WHERE kind = 'llm') AS llm_calls,
        coalesce(sum(input_tokens), 0) AS input_tokens,
        coalesce(sum(output_tokens), 0) AS output_tokens,
        coalesce(sum(cost_usd), 0) AS cost_usd,
        coalesce(array_agg(DISTINCT model ORDER BY model) FILTER (WHERE model IS NOT NULL),
                 '{}') AS models
    FROM ins
    GROUP BY trace_id
), merged AS (
INSERT INTO traces AS t (
    trace_id, has_root, name, source, service_name, client, start_time, end_time, status,
    span_count, llm_calls, input_tokens, output_tokens, cost_usd, models
)
SELECT trace_id, has_root, name, source, service_name, client, start_time, end_time, status,
       span_count, llm_calls, input_tokens, output_tokens, cost_usd, models
FROM agg
ORDER BY trace_id
ON CONFLICT (trace_id) DO UPDATE SET
    has_root = t.has_root OR excluded.has_root,
    name = CASE WHEN excluded.has_root AND NOT t.has_root THEN excluded.name ELSE t.name END,
    source = CASE WHEN excluded.has_root AND NOT t.has_root
                  THEN excluded.source ELSE t.source END,
    service_name = CASE WHEN excluded.has_root AND NOT t.has_root
                        THEN coalesce(excluded.service_name, t.service_name)
                        ELSE coalesce(t.service_name, excluded.service_name) END,
    client = CASE WHEN excluded.has_root AND NOT t.has_root
                  THEN coalesce(excluded.client, t.client)
                  ELSE coalesce(t.client, excluded.client) END,
    start_time = least(t.start_time, excluded.start_time),
    end_time = greatest(t.end_time, excluded.end_time),
    status = CASE WHEN lucentpad_status_rank(excluded.status) > lucentpad_status_rank(t.status)
                  THEN excluded.status ELSE t.status END,
    span_count = t.span_count + excluded.span_count,
    llm_calls = t.llm_calls + excluded.llm_calls,
    input_tokens = t.input_tokens + excluded.input_tokens,
    output_tokens = t.output_tokens + excluded.output_tokens,
    cost_usd = t.cost_usd + excluded.cost_usd,
    models = ARRAY(SELECT DISTINCT m FROM unnest(t.models || excluded.models) AS m ORDER BY m)
RETURNING 1
)
SELECT count(*) FROM ins
"""

_SUMMARY_COLUMNS = """
    trace_id, name, source, service_name, client, start_time, end_time, status,
    span_count, llm_calls, input_tokens, output_tokens, cost_usd, models
"""


class InvalidCursorError(ValueError):
    """The pagination cursor could not be decoded."""


@dataclass(frozen=True)
class Cursor:
    """Keyset position: the last row of the previous page in (start_time DESC, trace_id DESC)."""

    start_time: datetime
    trace_id: str

    def encode(self) -> str:
        raw = json.dumps({"t": self.start_time.isoformat(), "id": self.trace_id}).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @classmethod
    def decode(cls, token: str) -> Cursor:
        try:
            raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
            data = json.loads(raw)
            start_time = datetime.fromisoformat(data["t"])
            trace_id = data["id"]
        except (binascii.Error, ValueError, KeyError, TypeError) as exc:
            raise InvalidCursorError("invalid cursor") from exc
        if start_time.tzinfo is None or not isinstance(trace_id, str):
            raise InvalidCursorError("invalid cursor")
        return cls(start_time, trace_id)


def _int_attr(attrs: Attributes, key: str) -> int | None:
    value = attrs.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _extract(span: Span) -> tuple[str | None, int | None, int | None, float | None]:
    """Typed columns from OTel attributes: model, input/output tokens, cost.

    Cost is taken from ``lucentpad.cost_usd`` when the producer set it, otherwise computed
    from the (illustrative) price table when model and token counts are known.
    """
    attrs = span.attributes
    model: str | None = None
    for key in (Attr.GEN_AI_RESPONSE_MODEL, Attr.GEN_AI_REQUEST_MODEL):
        value = attrs.get(key)
        if isinstance(value, str) and value:
            model = value
            break
    in_tok = _int_attr(attrs, Attr.GEN_AI_INPUT_TOKENS)
    out_tok = _int_attr(attrs, Attr.GEN_AI_OUTPUT_TOKENS)
    raw_cost = attrs.get(Attr.COST_USD)
    cost: float | None = None
    if isinstance(raw_cost, int | float) and not isinstance(raw_cost, bool):
        cost = float(raw_cost)
    elif model is not None and (in_tok is not None or out_tok is not None):
        cost = cost_usd(model, in_tok or 0, out_tok or 0)
    return model, in_tok, out_tok, cost


def _record(span: Span) -> tuple[Any, ...]:
    model, in_tok, out_tok, cost = _extract(span)
    return (
        span.trace_id,
        span.span_id,
        span.parent_span_id,
        span.name,
        span.kind,
        span.source,
        span.start_time,
        span.end_time,
        span.status,
        span.status_message,
        model,
        in_tok,
        out_tok,
        None if cost is None else Decimal(str(cost)),
        dict(span.attributes),
        [e.model_dump(mode="json") for e in span.events],
    )


def _summary(row: asyncpg.Record) -> TraceSummary:
    start: datetime = row["start_time"]
    end: datetime = row["end_time"]
    return TraceSummary(
        trace_id=row["trace_id"],
        name=row["name"],
        source=row["source"],
        service_name=row["service_name"],
        client=row["client"],
        start_time=start,
        duration_ms=(end - start).total_seconds() * 1000,
        status=row["status"],
        span_count=row["span_count"],
        llm_calls=row["llm_calls"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        cost_usd=float(row["cost_usd"]),
        models=list(row["models"]),
    )


def _span(row: asyncpg.Record) -> Span:
    return Span.model_validate(
        {
            "trace_id": row["trace_id"],
            "span_id": row["span_id"],
            "parent_span_id": row["parent_span_id"],
            "name": row["name"],
            "kind": row["kind"],
            "source": row["source"],
            "start_time": row["start_time"],
            "end_time": row["end_time"],
            "status": row["status"],
            "status_message": row["status_message"],
            "attributes": row["attributes"],
            "events": row["events"],
        }
    )


class SpanStore:
    """Span and trace persistence over an asyncpg pool."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def insert_spans(self, spans: Iterable[Span]) -> int:
        """Bulk-insert spans; duplicates (same trace_id, span_id) are ignored.

        Returns the number of spans newly stored.
        """
        records = [_record(s) for s in spans]
        if not records:
            return 0
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(_CREATE_STAGE)
            await conn.copy_records_to_table("spans_stage", records=records, columns=_SPAN_COLUMNS)
            inserted = await conn.fetchval(_MERGE_STAGE)
        return int(inserted)

    async def is_empty(self) -> bool:
        async with self._pool.acquire() as conn:
            return not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM spans)")

    async def list_traces(
        self,
        *,
        limit: int,
        cursor: Cursor | None = None,
        source: SpanSource | None = None,
        status: SpanStatus | None = None,
    ) -> tuple[list[TraceSummary], Cursor | None]:
        """One page of traces, newest first, plus the cursor for the next page (or None)."""
        where: list[str] = []
        args: list[Any] = []
        if source is not None:
            args.append(source)
            where.append(f"source = ${len(args)}")
        if status is not None:
            args.append(status)
            where.append(f"status = ${len(args)}")
        if cursor is not None:
            args += [cursor.start_time, cursor.trace_id]
            where.append(f"(start_time, trace_id) < (${len(args) - 1}, ${len(args)})")
        args.append(limit + 1)
        sql = (
            f"SELECT {_SUMMARY_COLUMNS} FROM traces"  # noqa: S608 - only fixed fragments
            + (" WHERE " + " AND ".join(where) if where else "")
            + f" ORDER BY start_time DESC, trace_id DESC LIMIT ${len(args)}"
        )
        async with self._pool.acquire() as conn:
            rows: Sequence[asyncpg.Record] = await conn.fetch(sql, *args)
        page = [_summary(r) for r in rows[:limit]]
        next_cursor = (
            Cursor(rows[limit - 1]["start_time"], rows[limit - 1]["trace_id"])
            if len(rows) > limit
            else None
        )
        return page, next_cursor

    async def get_trace(self, trace_id: str) -> TraceDetail | None:
        """The trace summary and all its spans ordered by start_time, or None if unknown."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_SUMMARY_COLUMNS} FROM traces WHERE trace_id = $1",  # noqa: S608
                trace_id,
            )
            if row is None:
                return None
            spans = await conn.fetch(
                "SELECT * FROM spans WHERE trace_id = $1 ORDER BY start_time, span_id", trace_id
            )
        return TraceDetail(trace=_summary(row), spans=[_span(r) for r in spans])
