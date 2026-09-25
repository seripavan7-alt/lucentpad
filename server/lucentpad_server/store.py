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
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Final

import asyncpg

from lucentpad_server.pricing import cost_usd
from lucentpad_server.query import FACETS, TraceFilter
from lucentpad_server.schema import (
    FACET_MAX_VALUES,
    PREVIEW_MAX_CHARS,
    Attr,
    Attributes,
    FacetValue,
    Span,
    TraceDetail,
    TraceFacets,
    TraceList,
    TraceOrder,
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
# Previews ($1 = input attribute key, $2 = output key): candidates are the root and llm spans
# carrying the attribute, ranked by a key (root: -infinity for input, +infinity for output;
# llm span: its start_time). The lowest input key and the highest output key win, across
# batches too, so the root's previews win once it arrives (D9).
_MERGE_STAGE = """
WITH ins AS (
    INSERT INTO spans SELECT * FROM spans_stage
    ON CONFLICT (trace_id, span_id) DO NOTHING
    RETURNING *
), cand AS (
    SELECT
        ins.*,
        CASE WHEN (parent_span_id IS NULL OR kind = 'llm') AND attributes ->> $1 IS NOT NULL
             THEN CASE WHEN parent_span_id IS NULL THEN '-infinity'::timestamptz
                       ELSE start_time END
        END AS in_key,
        CASE WHEN (parent_span_id IS NULL OR kind = 'llm') AND attributes ->> $2 IS NOT NULL
             THEN CASE WHEN parent_span_id IS NULL THEN 'infinity'::timestamptz
                       ELSE start_time END
        END AS out_key
    FROM ins
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
                 '{}') AS models,
        (array_agg(attributes ->> $1 ORDER BY in_key, span_id)
            FILTER (WHERE in_key IS NOT NULL))[1] AS input_preview,
        min(in_key) AS input_preview_key,
        (array_agg(attributes ->> $2 ORDER BY out_key DESC, span_id DESC)
            FILTER (WHERE out_key IS NOT NULL))[1] AS output_preview,
        max(out_key) AS output_preview_key
    FROM cand
    GROUP BY trace_id
), merged AS (
INSERT INTO traces AS t (
    trace_id, has_root, name, source, service_name, client, start_time, end_time, status,
    span_count, llm_calls, input_tokens, output_tokens, cost_usd, models,
    input_preview, input_preview_key, output_preview, output_preview_key
)
SELECT trace_id, has_root, name, source, service_name, client, start_time, end_time, status,
       span_count, llm_calls, input_tokens, output_tokens, cost_usd, models,
       input_preview, input_preview_key, output_preview, output_preview_key
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
    models = ARRAY(SELECT DISTINCT m FROM unnest(t.models || excluded.models) AS m ORDER BY m),
    input_preview = CASE WHEN t.input_preview_key IS NULL
                              OR excluded.input_preview_key < t.input_preview_key
                         THEN excluded.input_preview ELSE t.input_preview END,
    input_preview_key = least(t.input_preview_key, excluded.input_preview_key),
    output_preview = CASE WHEN t.output_preview_key IS NULL
                               OR excluded.output_preview_key > t.output_preview_key
                          THEN excluded.output_preview ELSE t.output_preview END,
    output_preview_key = greatest(t.output_preview_key, excluded.output_preview_key),
    updated_at = now()
RETURNING 1
)
SELECT count(*) FROM ins
"""

_SUMMARY_COLUMNS = """
    trace_id, name, source, service_name, client, start_time, end_time, status,
    span_count, llm_calls, input_tokens, output_tokens, cost_usd, models,
    input_preview, output_preview
"""

# D11 sample shift ($1 = interval). Event times live inside the events jsonb array.
_SHIFT_SPANS = """
UPDATE spans SET
    start_time = start_time + $1,
    end_time = end_time + $1,
    events = CASE WHEN jsonb_array_length(events) = 0 THEN events ELSE (
        SELECT jsonb_agg(
            CASE WHEN e ? 'time'
                 THEN jsonb_set(e, '{time}', to_jsonb((e ->> 'time')::timestamptz + $1))
                 ELSE e END
            ORDER BY i)
        FROM jsonb_array_elements(events) WITH ORDINALITY AS x(e, i)
    ) END
"""
_SHIFT_TRACES = """
UPDATE traces SET
    start_time = start_time + $1,
    end_time = end_time + $1,
    input_preview_key = input_preview_key + $1,
    output_preview_key = output_preview_key + $1
"""

LIVE_OVERLAP: Final = timedelta(seconds=2)
"""``since`` polls look back this much before the client's ``since``, so a write that
committed just after the previous response is never missed; clients merge by id."""

META_SAMPLE_SEEDED: Final = "sample_seeded_at"
META_REAL_DATA: Final = "real_data_at"
_MARK_META = (
    "INSERT INTO lucentpad_meta (key, value) VALUES ($1, now()::text) ON CONFLICT (key) DO NOTHING"
)

# Filter column on ``traces`` per facet (``model`` is an array, handled separately).
_FACET_COLUMNS: Final = {
    "name": "name",
    "status": "status",
    "source": "source",
    "client": "client",
    "service": "service_name",
}


def _filter_sql(filters: TraceFilter, args: list[Any], *, exclude: str | None = None) -> list[str]:
    """WHERE conditions for ``filters``, appending their parameters to ``args``: the time
    window plus every facet filter except ``exclude``. OR within a filter, AND across."""
    where: list[str] = []
    if filters.start is not None:
        args.append(filters.start)
        where.append(f"start_time >= ${len(args)}")
    if filters.end is not None:
        args.append(filters.end)
        where.append(f"start_time < ${len(args)}")
    for facet in FACETS:
        values = filters.values(facet)
        if facet == exclude or not values:
            continue
        args.append(list(values))
        if facet == "model":
            where.append(f"models && ${len(args)}::text[]")
        else:
            where.append(f"{_FACET_COLUMNS[facet]} = ANY(${len(args)}::text[])")
    return where


def _where(conditions: list[str]) -> str:
    return " WHERE " + " AND ".join(conditions) if conditions else ""


class InvalidCursorError(ValueError):
    """The pagination cursor is malformed, or was issued for another order or filter set."""


@dataclass(frozen=True)
class Cursor:
    """Keyset position after the last row of a page, bound to the page's order and filters."""

    start_time: datetime
    trace_id: str
    order: TraceOrder = "desc"
    fingerprint: str = ""

    def encode(self) -> str:
        raw = json.dumps(
            {
                "t": self.start_time.isoformat(),
                "id": self.trace_id,
                "o": self.order,
                "f": self.fingerprint,
            }
        ).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @classmethod
    def decode(cls, token: str) -> Cursor:
        try:
            raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
            data = json.loads(raw)
            start_time = datetime.fromisoformat(data["t"])
            trace_id = data["id"]
            order = data.get("o", "desc")
            fingerprint = data.get("f", "")
        except (binascii.Error, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise InvalidCursorError("invalid cursor") from exc
        if (
            start_time.tzinfo is None
            or not isinstance(trace_id, str)
            or order not in ("desc", "asc")
            or not isinstance(fingerprint, str)
        ):
            raise InvalidCursorError("invalid cursor")
        return cls(start_time, trace_id, order, fingerprint)


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


def _cap_previews(attrs: Attributes) -> Attributes:
    """Guard: previews longer than ``PREVIEW_MAX_CHARS`` are cut and flagged as truncated."""
    capped = dict(attrs)
    for key, flag in (
        (Attr.INPUT_PREVIEW, Attr.INPUT_TRUNCATED),
        (Attr.OUTPUT_PREVIEW, Attr.OUTPUT_TRUNCATED),
    ):
        value = capped.get(key)
        if isinstance(value, str) and len(value) > PREVIEW_MAX_CHARS:
            capped[key] = value[:PREVIEW_MAX_CHARS]
            capped[flag] = True
    return capped


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
        _cap_previews(span.attributes),
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
        input_preview=row["input_preview"],
        output_preview=row["output_preview"],
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
            "attributes": _with_cost(row["attributes"], row["cost_usd"]),
            "events": row["events"],
        }
    )


def _with_cost(attributes: dict[str, Any], cost: Decimal | None) -> dict[str, Any]:
    """Expose the server-computed cost (SDK spans never carry one) as ``lucentpad.cost_usd``,
    so every priced llm span shows its cost. A producer-set value is left as it was."""
    if cost is None or Attr.COST_USD in attributes:
        return attributes
    return {**attributes, Attr.COST_USD: float(cost)}


class SpanStore:
    """Span and trace persistence over an asyncpg pool."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._real_data_marked = False

    async def insert_spans(self, spans: Iterable[Span], *, sample: bool = False) -> int:
        """Bulk-insert spans; duplicates (same trace_id, span_id) are ignored.

        ``sample=True`` records the batch as startup sample data; any other insert marks the
        database as holding real data, which stops the sample time shift for good (D11).
        Returns the number of spans newly stored.
        """
        records = [_record(s) for s in spans]
        if not records:
            return 0
        mark = META_SAMPLE_SEEDED if sample else None
        if not sample and not self._real_data_marked:
            mark = META_REAL_DATA
        async with self._pool.acquire() as conn, conn.transaction():
            if mark is not None:
                # First statement, so it waits for a concurrent sample shift to commit.
                await conn.execute(_MARK_META, mark)
            await conn.execute(_CREATE_STAGE)
            await conn.copy_records_to_table("spans_stage", records=records, columns=_SPAN_COLUMNS)
            inserted = await conn.fetchval(_MERGE_STAGE, Attr.INPUT_PREVIEW, Attr.OUTPUT_PREVIEW)
        if mark == META_REAL_DATA:
            self._real_data_marked = True
        return int(inserted)

    async def is_empty(self) -> bool:
        async with self._pool.acquire() as conn:
            return not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM spans)")

    async def shift_sample_to_now(
        self,
        *,
        newest_age: timedelta = timedelta(seconds=60),
        min_shift: timedelta = timedelta(seconds=1),
    ) -> timedelta | None:
        """D11: if the database holds only startup sample data, move every span and trace
        timestamp (event times included) so the newest trace started ``newest_age`` ago.

        One transaction. Returns the shift applied, or None when the database is not
        sample-only, is empty, or is already within ``min_shift`` of the target.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            # Self-conflicting and conflicting with inserts' ROW EXCLUSIVE lock: a concurrent
            # first real insert either commits before this check or waits for the shift.
            await conn.execute("LOCK TABLE lucentpad_meta IN SHARE ROW EXCLUSIVE MODE")
            flags = {r["key"] for r in await conn.fetch("SELECT key FROM lucentpad_meta")}
            if META_SAMPLE_SEEDED not in flags or META_REAL_DATA in flags:
                return None
            delta: timedelta | None = await conn.fetchval(
                "SELECT now() - $1::interval - max(start_time) FROM traces", newest_age
            )
            if delta is None or abs(delta) < min_shift:
                return None
            await conn.execute(_SHIFT_SPANS, delta)
            await conn.execute(_SHIFT_TRACES, delta)
            await conn.execute(
                """
                INSERT INTO lucentpad_meta (key, value) VALUES ('sample_shifted_at', now()::text)
                ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = now()
                """
            )
        return delta

    async def list_traces(
        self,
        filters: TraceFilter,
        *,
        limit: int,
        cursor: str | None = None,
        order: TraceOrder = "desc",
        since: datetime | None = None,
    ) -> TraceList:
        """One page of traces in ``order`` plus the next cursor. Raises ``InvalidCursorError``
        for a cursor from another order or filter set.

        With ``since`` (live polling): only traces updated after ``since - LIVE_OVERLAP``, at
        most ``limit`` of them in ``order``, and never a next cursor.
        """
        fingerprint = filters.fingerprint()
        after = Cursor.decode(cursor) if cursor is not None else None
        if after is not None and (after.order != order or after.fingerprint != fingerprint):
            raise InvalidCursorError("cursor was issued for another order or filter set")
        args: list[Any] = []
        where = _filter_sql(filters, args)
        if since is not None:
            args.append(since - LIVE_OVERLAP)
            where.append(f"updated_at > ${len(args)}")
        cmp, direction = (">", "ASC") if order == "asc" else ("<", "DESC")
        if after is not None:
            args += [after.start_time, after.trace_id]
            where.append(f"(start_time, trace_id) {cmp} (${len(args) - 1}, ${len(args)})")
        args.append(limit + 1)
        sql = (
            f"SELECT {_SUMMARY_COLUMNS}, now() AS as_of FROM traces"  # noqa: S608 - fixed fragments
            + _where(where)
            + f" ORDER BY start_time {direction}, trace_id {direction} LIMIT ${len(args)}"
        )
        async with self._pool.acquire() as conn:
            rows: Sequence[asyncpg.Record] = await conn.fetch(sql, *args)
            as_of: datetime = rows[0]["as_of"] if rows else await conn.fetchval("SELECT now()")
        page = [_summary(r) for r in rows[:limit]]
        next_cursor = (
            Cursor(rows[limit - 1]["start_time"], rows[limit - 1]["trace_id"], order, fingerprint)
            if len(rows) > limit and since is None
            else None
        )
        return TraceList(
            traces=page,
            next_cursor=next_cursor.encode() if next_cursor is not None else None,
            as_of=as_of,
        )

    async def facets(self, filters: TraceFilter) -> TraceFacets:
        """Value counts per facet, each applying every filter except its own; at most
        ``FACET_MAX_VALUES`` per facet, by count descending, then value (code point order)."""
        args: list[Any] = []
        parts: list[str] = []
        for facet in FACETS:
            where = _filter_sql(filters, args, exclude=facet)
            if facet == "model":
                source, column = "traces CROSS JOIN LATERAL unnest(models) AS m", "m"
            else:
                source, column = "traces", _FACET_COLUMNS[facet]
                where = [f"{column} IS NOT NULL", *where]
            parts.append(
                f"(SELECT '{facet}' AS facet, {column} AS value, count(*) AS n"  # noqa: S608
                f" FROM {source}{_where(where)} GROUP BY {column}"
                f' ORDER BY n DESC, {column} COLLATE "C" LIMIT {FACET_MAX_VALUES})'
            )
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(" UNION ALL ".join(parts), *args)
        values: dict[str, list[FacetValue]] = {facet: [] for facet in FACETS}
        for r in rows:
            values[r["facet"]].append(FacetValue(value=r["value"], count=r["n"]))
        for items in values.values():
            items.sort(key=lambda v: (-v.count, v.value))
        return TraceFacets.model_validate(values)

    async def get_trace(
        self, trace_id: str, *, since: datetime | None = None
    ) -> TraceDetail | None:
        """The trace summary and its spans ordered by start_time (with ``since``, only spans
        stored after ``since - LIVE_OVERLAP``), or None if unknown."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_SUMMARY_COLUMNS}, now() AS as_of FROM traces WHERE trace_id = $1",  # noqa: S608
                trace_id,
            )
            if row is None:
                return None
            if since is None:
                spans = await conn.fetch(
                    "SELECT * FROM spans WHERE trace_id = $1 ORDER BY start_time, span_id",
                    trace_id,
                )
            else:
                spans = await conn.fetch(
                    "SELECT * FROM spans WHERE trace_id = $1 AND stored_at > $2"
                    " ORDER BY start_time, span_id",
                    trace_id,
                    since - LIVE_OVERLAP,
                )
        return TraceDetail(trace=_summary(row), spans=[_span(r) for r in spans], as_of=row["as_of"])
