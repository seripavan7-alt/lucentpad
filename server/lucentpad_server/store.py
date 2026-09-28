"""Repository layer: write spans, read trace summaries and trace detail.

Writes go through a per-connection temp staging table filled with binary ``COPY``; a single
statement then moves new rows into ``spans`` (skipping duplicates) and folds exactly those
new rows into the pre-aggregated ``traces`` table. Re-sent spans are therefore idempotent
and never double-count tokens or cost.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import secrets
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Final, get_args

import asyncpg

from lucentpad_server.pricing import cost_usd
from lucentpad_server.query import FACETS, TraceFilter
from lucentpad_server.schema import (
    FACET_MAX_VALUES,
    PREVIEW_MAX_CHARS,
    Attr,
    Attributes,
    CostGroup,
    CostPoint,
    CostSeries,
    DataInfo,
    EvalRun,
    EvalRunIn,
    EvalRunList,
    EvalRunSummary,
    EventName,
    FacetValue,
    GatewayClientTotals,
    GatewayProvider,
    GatewaySummary,
    GatewayTurn,
    GatewayTurnList,
    GuardrailEvent,
    GuardrailEventKind,
    GuardrailEventList,
    GuardrailRuleCount,
    GuardrailSummary,
    Span,
    TraceDetail,
    TraceFacets,
    TraceList,
    TraceOrder,
    TraceSort,
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
    "sample",
)

_CREATE_STAGE = """
CREATE TEMP TABLE IF NOT EXISTS spans_stage (LIKE spans INCLUDING DEFAULTS) ON COMMIT DELETE ROWS
"""

# Guardrail events (`gev`, M3): each new kind='guardrail' span and each `lucentpad.redaction` event
# of a new span becomes a guardrail_events row (migration 0006 has the same projection for its
# backfill). Attribute keys are inlined literals: Attr.CLIENT, GUARDRAIL_RULE, GUARDRAIL_REASON,
# REDACTION_KIND, REDACTION_COUNT and EventName.REDACTION (test_store_guardrails checks them).
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
        bool_or(sample) AS sample,
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
    input_preview, input_preview_key, output_preview, output_preview_key, sample
)
SELECT trace_id, has_root, name, source, service_name, client, start_time, end_time, status,
       span_count, llm_calls, input_tokens, output_tokens, cost_usd, models,
       input_preview, input_preview_key, output_preview, output_preview_key, sample
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
    sample = t.sample OR excluded.sample,
    updated_at = now()
RETURNING 1
), gev AS (
INSERT INTO guardrail_events
    (trace_id, span_id, seq, kind, time, source, client, rule, reason, redaction_kind, count,
     sample, budget_limit_usd, budget_spent_usd, budget_scope)
SELECT trace_id, span_id, 0, 'block', start_time, source, attributes ->> 'lucentpad.client',
       attributes ->> 'lucentpad.guardrail.rule',
       coalesce(attributes ->> 'lucentpad.guardrail.reason', status_message),
       NULL, 1, sample, NULL::float8, NULL::float8, NULL
FROM ins WHERE kind = 'guardrail'
UNION ALL
SELECT i.trace_id, i.span_id, e.seq::integer, 'redaction', (e.ev ->> 'time')::timestamptz,
       i.source, i.attributes ->> 'lucentpad.client', NULL, NULL,
       left(coalesce(e.ev -> 'attributes' ->> 'lucentpad.redaction.kind', 'unknown'), 100),
       CASE WHEN jsonb_typeof(e.ev -> 'attributes' -> 'lucentpad.redaction.count') = 'number'
            THEN least(greatest((e.ev -> 'attributes' ->> 'lucentpad.redaction.count')::numeric,
                                0), 2147483647)::integer
            ELSE 1 END,
       i.sample, NULL, NULL, NULL
FROM ins AS i
CROSS JOIN LATERAL jsonb_array_elements(i.events) WITH ORDINALITY AS e(ev, seq)
WHERE i.events @> '[{"name": "lucentpad.redaction"}]'
  AND e.ev ->> 'name' = 'lucentpad.redaction'
UNION ALL
SELECT i.trace_id, i.span_id, e.seq::integer, 'budget', (e.ev ->> 'time')::timestamptz,
       i.source, i.attributes ->> 'lucentpad.client', NULL, NULL, NULL, 1, i.sample,
       CASE WHEN jsonb_typeof(e.ev -> 'attributes' -> 'lucentpad.budget.limit_usd') = 'number'
            THEN (e.ev -> 'attributes' ->> 'lucentpad.budget.limit_usd')::float8 END,
       CASE WHEN jsonb_typeof(e.ev -> 'attributes' -> 'lucentpad.budget.spent_usd') = 'number'
            THEN (e.ev -> 'attributes' ->> 'lucentpad.budget.spent_usd')::float8 END,
       left(e.ev -> 'attributes' ->> 'lucentpad.budget.scope', 20)
FROM ins AS i
CROSS JOIN LATERAL jsonb_array_elements(i.events) WITH ORDINALITY AS e(ev, seq)
WHERE i.events @> '[{"name": "lucentpad.budget.alert"}]'
  AND e.ev ->> 'name' = 'lucentpad.budget.alert'
ON CONFLICT DO NOTHING
RETURNING 1
)
SELECT count(*) FROM ins
"""

_SUMMARY_COLUMNS = """
    trace_id, name, source, service_name, client, start_time, end_time, status,
    span_count, llm_calls, input_tokens, output_tokens, cost_usd, models,
    input_preview, output_preview, sample
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
_SHIFT_OTHERS = (
    "UPDATE guardrail_events SET time = time + $1",
    "UPDATE eval_runs SET started_at = started_at + $1 WHERE sample",
)

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
    where: list[str] = ["NOT sample"] if filters.hide_sample else []
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
    """The pagination cursor is malformed, or was issued for another sort, order or filter set."""


SortKey = datetime | float | Decimal | str
"""A cursor's sort key value: start_time (datetime), duration_ms (float), cost_usd (Decimal,
exact like its numeric column) or name / source (str)."""

# ORDER BY expression per sort (every one NOT NULL); name and source compare in byte order.
_SORT_SQL: Final[dict[TraceSort, str]] = {
    "started": "start_time",
    "duration": "duration_ms",
    "name": 'name COLLATE "C"',
    "source": 'source COLLATE "C"',
    "cost": "cost_usd",
}
# Result column holding the sort key, and the parameter cast for keyset comparisons.
_SORT_COLUMN: Final[dict[TraceSort, str]] = {
    "started": "start_time",
    "duration": "duration_ms",
    "name": "name",
    "source": "source",
    "cost": "cost_usd",
}
_SORT_CAST: Final[dict[TraceSort, str]] = {
    "started": "timestamptz",
    "duration": "float8",
    "name": "text",
    "source": "text",
    "cost": "numeric",
}


def _encode_key(key: SortKey) -> str | float:
    if isinstance(key, datetime):
        return key.isoformat()
    if isinstance(key, Decimal):
        return str(key)
    return key


def _decode_key(sort: TraceSort, raw: object) -> SortKey:
    """The typed sort key of a cursor; ValueError when it doesn't fit ``sort``."""
    if sort == "started" and isinstance(raw, str):
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is not None:
            return parsed
    elif sort == "duration" and isinstance(raw, int | float) and not isinstance(raw, bool):
        if math.isfinite(raw):
            return float(raw)
    elif sort == "cost" and isinstance(raw, str):
        try:
            cost = Decimal(raw)
        except ArithmeticError:
            raise ValueError("bad cost key") from None
        if cost.is_finite():
            return cost
    elif sort in ("name", "source") and isinstance(raw, str):
        return raw
    raise ValueError("cursor key does not match its sort")


@dataclass(frozen=True)
class Cursor:
    """Keyset position after the last row of a page, bound to the page's sort, order and
    filters. ``key`` is that row's sort key (see ``SortKey``); ties break on ``trace_id``."""

    key: SortKey
    trace_id: str
    sort: TraceSort = "started"
    order: TraceOrder = "desc"
    fingerprint: str = ""

    def encode(self) -> str:
        raw = json.dumps(
            {
                "s": self.sort,
                "k": _encode_key(self.key),
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
            sort = data["s"]
            if sort not in _SORT_SQL:
                raise ValueError("unknown sort")
            key = _decode_key(sort, data["k"])
            trace_id = data["id"]
            order = data["o"]
            fingerprint = data["f"]
        except (binascii.Error, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise InvalidCursorError("invalid cursor") from exc
        if (
            not isinstance(trace_id, str)
            or order not in ("desc", "asc")
            or not isinstance(fingerprint, str)
        ):
            raise InvalidCursorError("invalid cursor")
        return cls(key, trace_id, sort, order, fingerprint)


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
        cost = cost_usd(
            model,
            in_tok or 0,
            out_tok or 0,
            _int_attr(attrs, Attr.GEN_AI_CACHE_READ_TOKENS) or 0,
            _int_attr(attrs, Attr.GEN_AI_CACHE_CREATION_TOKENS) or 0,
        )
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


def _record(span: Span, sample: bool = False) -> tuple[Any, ...]:
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
        sample,
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
        sample=row["sample"],
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


# --------------------------------------------------------------------------- gateway (M2)


def _sql_str(value: str) -> str:
    """A fixed attribute key as a SQL literal (inlined so the partial/expression indexes of
    migration 0004 match the query text)."""
    if "'" in value or "\\" in value:
        raise ValueError(value)
    return f"'{value}'"


# The partial-index predicate of migration 0004: a gateway turn.
_GW_TURN = "source = 'gateway' AND kind = 'llm'"
# Client of a turn: the span's own attribute (the gateway sets it on every span it writes).
_GW_CLIENT = f"(attributes ->> {_sql_str(Attr.CLIENT)})"
_GW_FAILOVER = json.dumps([{"name": EventName.FAILOVER}])
_GW_TURN_COLUMNS = f"""
    trace_id, span_id, start_time, status, model, input_tokens, output_tokens, cost_usd,
    {_GW_CLIENT} AS client,
    attributes ->> {_sql_str(Attr.GATEWAY_UPSTREAM)} AS upstream,
    attributes ->> {_sql_str(Attr.GEN_AI_SYSTEM)} AS system,
    attributes -> {_sql_str(Attr.TTFB_MS)} AS ttfb_ms,
    attributes -> {_sql_str(Attr.STREAMING)} AS streaming,
    attributes ->> {_sql_str(Attr.INPUT_PREVIEW)} AS input_preview,
    attributes ->> {_sql_str(Attr.OUTPUT_PREVIEW)} AS output_preview,
    EXTRACT(EPOCH FROM (end_time - start_time)) * 1000 AS duration_ms,
    events @> {_sql_str(_GW_FAILOVER)}::jsonb AS failover
"""
_PROVIDERS: Final[dict[str, GatewayProvider]] = {"anthropic": "anthropic", "openai": "openai"}


def _gateway_window(
    start: datetime | None, end: datetime | None, args: list[Any], hide_sample: bool = False
) -> list[str]:
    where = [_GW_TURN, *(["NOT sample"] if hide_sample else [])]
    if start is not None:
        args.append(start)
        where.append(f"start_time >= ${len(args)}")
    if end is not None:
        args.append(end)
        where.append(f"start_time < ${len(args)}")
    return where


def _gateway_fingerprint(
    start: datetime | None,
    end: datetime | None,
    clients: tuple[str, ...],
    hide_sample: bool = False,
) -> str:
    data = {
        "from": start.isoformat() if start else None,
        "to": end.isoformat() if end else None,
        "client": sorted(set(clients)),
        **({"hide_sample": True} if hide_sample else {}),
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class GatewayCursor:
    """Keyset position after the last turn of a page (newest first), bound to its filters."""

    start_time: datetime
    span_id: str
    trace_id: str
    fingerprint: str

    def encode(self) -> str:
        raw = json.dumps(
            {
                "g": 1,
                "k": self.start_time.isoformat(),
                "s": self.span_id,
                "t": self.trace_id,
                "f": self.fingerprint,
            }
        ).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @classmethod
    def decode(cls, token: str) -> GatewayCursor:
        try:
            data = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
            if data["g"] != 1:
                raise ValueError("not a gateway cursor")
            start_time = datetime.fromisoformat(data["k"])
            span_id, trace_id, fingerprint = data["s"], data["t"], data["f"]
        except (binascii.Error, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise InvalidCursorError("invalid cursor") from exc
        if start_time.tzinfo is None or not all(
            isinstance(v, str) for v in (span_id, trace_id, fingerprint)
        ):
            raise InvalidCursorError("invalid cursor")
        return cls(start_time, span_id, trace_id, fingerprint)


_GW_ORDER = "ORDER BY start_time DESC, span_id DESC, trace_id DESC"


def _gateway_turns_sql(
    start: datetime | None,
    end: datetime | None,
    clients: tuple[str, ...],
    since: datetime | None,
    after: GatewayCursor | None,
    limit: int,
    hide_sample: bool = False,
) -> tuple[str, list[Any]]:
    """The turns query and its parameters. A client filter becomes one ordered, limited branch
    per client (``client = $n``, served in page order by ``spans_gateway_client_idx``) merged by
    the outer ORDER BY: Postgres 16 can't keep index order for ``= ANY(...)``, so a filter on a
    rare client would otherwise sort every matching turn."""
    args: list[Any] = []
    where = _gateway_window(start, end, args, hide_sample)
    if since is not None:
        args.append(since - LIVE_OVERLAP)
        where.append(f"stored_at > ${len(args)}")
    if after is not None:
        args += [after.start_time, after.span_id, after.trace_id]
        n = len(args)
        where.append(f"(start_time, span_id, trace_id) < (${n - 2}::timestamptz, ${n - 1}, ${n})")
    args.append(limit)
    limit_sql = f"LIMIT ${len(args)}"
    select = f"SELECT {_GW_TURN_COLUMNS} FROM spans"  # noqa: S608 - fixed fragments
    if not clients:
        plain = f"SELECT {_GW_TURN_COLUMNS}, now() AS as_of FROM spans"  # noqa: S608
        return f"{plain}{_where(where)} {_GW_ORDER} {limit_sql}", args
    branches: list[str] = []
    for client in sorted(set(clients)):
        args.append(client)
        cond = [*where, f"{_GW_CLIENT} = ${len(args)}"]
        branches.append(f"({select}{_where(cond)} {_GW_ORDER} {limit_sql})")
    union = " UNION ALL ".join(branches)
    return f"SELECT *, now() AS as_of FROM ({union}) AS u {_GW_ORDER} {limit_sql}", args  # noqa: S608


def _gateway_summary_sql(
    start: datetime | None, end: datetime | None, hide_sample: bool = False
) -> tuple[str, list[Any]]:
    """Per-client totals: first per (client, trace) (hash aggregate, no DISTINCT sort), then
    per client. Most expensive first, ties by client in code point order."""
    args: list[Any] = []
    where = _gateway_window(start, end, args, hide_sample)
    sql = (
        "SELECT client, count(*) AS sessions, sum(turns) AS turns,"  # noqa: S608 - fixed fragments
        " sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,"
        " sum(cost_usd) AS cost_usd FROM ("
        f"SELECT coalesce({_GW_CLIENT}, 'other') AS client, trace_id, count(*) AS turns,"
        " coalesce(sum(input_tokens), 0) AS input_tokens,"
        " coalesce(sum(output_tokens), 0) AS output_tokens,"
        " coalesce(sum(cost_usd), 0) AS cost_usd"
        f" FROM spans{_where(where)} GROUP BY 1, 2"
        ') AS per_trace GROUP BY client ORDER BY cost_usd DESC, client COLLATE "C"'
    )
    return sql, args


def _number(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def _gateway_turn(row: asyncpg.Record) -> GatewayTurn:
    upstream, system = row["upstream"], row["system"]
    provider = _PROVIDERS.get(upstream or "") or _PROVIDERS.get(system or "")
    cost: Decimal | None = row["cost_usd"]
    return GatewayTurn(
        trace_id=row["trace_id"],
        span_id=row["span_id"],
        client=row["client"],
        provider=provider,
        model=row["model"],
        start_time=row["start_time"],
        duration_ms=float(row["duration_ms"]),
        ttfb_ms=_number(row["ttfb_ms"]),
        status=row["status"],
        streaming=row["streaming"] is True,
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        cost_usd=None if cost is None else float(cost),
        failover=row["failover"],
        input_preview=row["input_preview"],
        output_preview=row["output_preview"],
    )


# --------------------------------------------------------------------------- M3


def _events_window(
    start: datetime | None, end: datetime | None, args: list[Any], hide_sample: bool
) -> list[str]:
    where = ["NOT sample"] if hide_sample else []
    if start is not None:
        args.append(start)
        where.append(f"time >= ${len(args)}")
    if end is not None:
        args.append(end)
        where.append(f"time < ${len(args)}")
    return where


def _events_fingerprint(
    start: datetime | None,
    end: datetime | None,
    kinds: tuple[str, ...],
    hide_sample: bool,
) -> str:
    wanted = sorted(set(kinds))
    data = {
        "from": start.isoformat() if start else None,
        "to": end.isoformat() if end else None,
        # Every kind is the same query as none.
        "kind": wanted if len(wanted) < len(get_args(GuardrailEventKind)) else [],
        "hide_sample": hide_sample,
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class EventCursor:
    """Keyset position after the last guardrail event of a page, bound to its filters."""

    time: datetime
    trace_id: str
    span_id: str
    seq: int
    fingerprint: str

    def encode(self) -> str:
        raw = json.dumps(
            {
                "e": 1,
                "k": self.time.isoformat(),
                "t": self.trace_id,
                "s": self.span_id,
                "q": self.seq,
                "f": self.fingerprint,
            }
        ).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @classmethod
    def decode(cls, token: str) -> EventCursor:
        try:
            data = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
            if data["e"] != 1:
                raise ValueError("not an events cursor")
            time = datetime.fromisoformat(data["k"])
            trace_id, span_id, seq, fingerprint = data["t"], data["s"], data["q"], data["f"]
        except (binascii.Error, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise InvalidCursorError("invalid cursor") from exc
        if (
            time.tzinfo is None
            or not all(isinstance(v, str) for v in (trace_id, span_id, fingerprint))
            or not isinstance(seq, int)
            or isinstance(seq, bool)
            or not 0 <= seq < 2**31
        ):
            raise InvalidCursorError("invalid cursor")
        return cls(time, trace_id, span_id, seq, fingerprint)


def _guardrail_event(row: asyncpg.Record) -> GuardrailEvent:
    return GuardrailEvent(
        kind=row["kind"],
        time=row["time"],
        trace_id=row["trace_id"],
        span_id=row["span_id"],
        source=row["source"],
        client=row["client"],
        rule=row["rule"],
        reason=row["reason"],
        redaction_kind=row["redaction_kind"],
        count=row["count"],
        budget_limit_usd=row["budget_limit_usd"],
        budget_spent_usd=row["budget_spent_usd"],
        budget_scope=row["budget_scope"],
    )


def bucket_seconds_for(window: timedelta) -> int:
    """Cost series bucket size: 5 minutes up to a 2-hour window, 1 hour up to 2 days, else
    1 day."""
    if window <= timedelta(hours=2):
        return 300
    if window <= timedelta(days=2):
        return 3600
    return 86400


_COST_GROUPS: Final[dict[CostGroup, str]] = {
    "model": "model",
    "client": f"(attributes ->> {_sql_str(Attr.CLIENT)})",
    "service": f"(attributes ->> {_sql_str(Attr.SERVICE_NAME)})",
}

_EVAL_IN_COLUMNS: Final = (
    "suite",
    "status",
    "started_at",
    "duration_ms",
    "model",
    "git_sha",
    "git_ref",
    "ci_url",
    "cost_usd",
    "baseline_cost_usd",
    "cases",
)
_EVAL_COLUMNS: Final = (
    "id",
    *_EVAL_IN_COLUMNS[:-1],
    "passed",
    "failed",
    "regressions",
    "cases",
    "sample",
)


def _sample_run_id(run: EvalRunIn) -> str:
    key = f"lucentpad-sample-eval\0{run.suite}\0{run.started_at.isoformat()}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class EvalCursor:
    """Keyset position after the last eval run of a page, bound to its suite filter."""

    started_at: datetime
    run_id: str
    fingerprint: str

    def encode(self) -> str:
        raw = json.dumps(
            {"v": 1, "k": self.started_at.isoformat(), "id": self.run_id, "f": self.fingerprint}
        ).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @classmethod
    def decode(cls, token: str) -> EvalCursor:
        try:
            data = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
            if data["v"] != 1:
                raise ValueError("not an eval cursor")
            started_at = datetime.fromisoformat(data["k"])
            run_id, fingerprint = data["id"], data["f"]
        except (binascii.Error, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise InvalidCursorError("invalid cursor") from exc
        if started_at.tzinfo is None or not all(isinstance(v, str) for v in (run_id, fingerprint)):
            raise InvalidCursorError("invalid cursor")
        return cls(started_at, run_id, fingerprint)


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
        records = [_record(s, sample) for s in spans]
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

    async def data_info(self) -> DataInfo:
        """Whether the database holds sample traces, real ones, or both."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT EXISTS (SELECT 1 FROM traces WHERE sample) AS sample_data,"
                " EXISTS (SELECT 1 FROM traces WHERE NOT sample) AS real_data"
            )
        assert row is not None
        return DataInfo(sample_data=row["sample_data"], real_data=row["real_data"])

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
            for statement in _SHIFT_OTHERS:
                await conn.execute(statement, delta)
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
        sort: TraceSort = "started",
        order: TraceOrder = "desc",
        since: datetime | None = None,
    ) -> TraceList:
        """One page of traces sorted by ``sort`` in ``order`` (ties on ``trace_id``, same
        direction) plus the next cursor. Raises ``InvalidCursorError`` for a cursor from another
        sort, order or filter set.

        With ``since`` (live polling): only traces updated after ``since - LIVE_OVERLAP``, at
        most ``limit`` of them in the requested sort, and never a next cursor.
        """
        fingerprint = filters.fingerprint()
        after = Cursor.decode(cursor) if cursor is not None else None
        if after is not None and (
            after.sort != sort or after.order != order or after.fingerprint != fingerprint
        ):
            raise InvalidCursorError("cursor was issued for another sort, order or filter set")
        args: list[Any] = []
        where = _filter_sql(filters, args)
        if since is not None:
            args.append(since - LIVE_OVERLAP)
            where.append(f"updated_at > ${len(args)}")
        key_sql, key_column = _SORT_SQL[sort], _SORT_COLUMN[sort]
        cmp, direction = (">", "ASC") if order == "asc" else ("<", "DESC")
        if after is not None:
            args += [after.key, after.trace_id]
            where.append(
                f"({key_sql}, trace_id) {cmp} (${len(args) - 1}::{_SORT_CAST[sort]}, ${len(args)})"
            )
        args.append(limit + 1)
        sql = (
            f"SELECT {_SUMMARY_COLUMNS}, duration_ms, now() AS as_of FROM traces"  # noqa: S608 - fixed fragments
            + _where(where)
            + f" ORDER BY {key_sql} {direction}, trace_id {direction} LIMIT ${len(args)}"
        )
        async with self._pool.acquire() as conn:
            rows: Sequence[asyncpg.Record] = await conn.fetch(sql, *args)
            as_of: datetime = rows[0]["as_of"] if rows else await conn.fetchval("SELECT now()")
        page = [_summary(r) for r in rows[:limit]]
        next_cursor = None
        if len(rows) > limit and since is None:
            last = rows[limit - 1]
            next_cursor = Cursor(last[key_column], last["trace_id"], sort, order, fingerprint)
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

    # ------------------------------------------------------------------ gateway (M2)

    async def gateway_turns(
        self,
        *,
        start: datetime | None,
        end: datetime | None,
        clients: tuple[str, ...],
        limit: int,
        cursor: str | None,
        since: datetime | None,
        hide_sample: bool = False,
    ) -> GatewayTurnList:
        """Gateway llm spans (one turn each) with ``start_time`` in [start, end), newest first
        by (start_time, span_id, trace_id), optionally only the given ``lucentpad.client``
        values (OR). Raises ``InvalidCursorError`` for a cursor from another filter set.

        With ``since`` (live polling): only turns stored after ``since - LIVE_OVERLAP``, at
        most ``limit`` of them, and never a next cursor.
        """
        fingerprint = _gateway_fingerprint(start, end, clients, hide_sample)
        after = GatewayCursor.decode(cursor) if cursor is not None else None
        if after is not None and after.fingerprint != fingerprint:
            raise InvalidCursorError("cursor was issued for another filter set")
        sql, args = _gateway_turns_sql(start, end, clients, since, after, limit + 1, hide_sample)
        async with self._pool.acquire() as conn:
            rows: Sequence[asyncpg.Record] = await conn.fetch(sql, *args)
            as_of: datetime = rows[0]["as_of"] if rows else await conn.fetchval("SELECT now()")
        next_cursor = None
        if len(rows) > limit and since is None:
            last = rows[limit - 1]
            next_cursor = GatewayCursor(
                last["start_time"], last["span_id"], last["trace_id"], fingerprint
            ).encode()
        return GatewayTurnList(
            turns=[_gateway_turn(r) for r in rows[:limit]], next_cursor=next_cursor, as_of=as_of
        )

    async def gateway_summary(
        self, *, start: datetime | None, end: datetime | None, hide_sample: bool = False
    ) -> GatewaySummary:
        """Per-client totals over gateway turns with ``start_time`` in [start, end): sessions
        (distinct traces with a turn in the window), turns, tokens and cost. Most expensive
        first, ties by client (code point order). Turns without a client count as ``other``."""
        sql, args = _gateway_summary_sql(start, end, hide_sample)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
            as_of: datetime = await conn.fetchval("SELECT now()")
        return GatewaySummary(
            clients=[
                GatewayClientTotals(
                    client=r["client"],
                    sessions=r["sessions"],
                    turns=r["turns"],
                    input_tokens=r["input_tokens"],
                    output_tokens=r["output_tokens"],
                    cost_usd=float(r["cost_usd"]),
                )
                for r in rows
            ],
            as_of=as_of,
        )

    # ------------------------------------------------------------------ M3: guardrails

    async def guardrail_events(
        self,
        *,
        start: datetime | None,
        end: datetime | None,
        kinds: tuple[GuardrailEventKind, ...],
        limit: int,
        cursor: str | None,
        since: datetime | None,
        hide_sample: bool = False,
    ) -> GuardrailEventList:
        """Blocks and redactions with ``time`` in [start, end), newest first by (time, trace_id,
        span_id, seq), optionally only the given kinds. Raises ``InvalidCursorError`` for a
        cursor from another filter set.

        With ``since`` (live polling): only events stored after ``since - LIVE_OVERLAP``, at most
        ``limit`` of them, and never a next cursor.
        """
        fingerprint = _events_fingerprint(start, end, kinds, hide_sample)
        after = EventCursor.decode(cursor) if cursor is not None else None
        if after is not None and after.fingerprint != fingerprint:
            raise InvalidCursorError("cursor was issued for another filter set")
        args: list[Any] = []
        where = _events_window(start, end, args, hide_sample)
        wanted = sorted(set(kinds))
        if 0 < len(wanted) < len(get_args(GuardrailEventKind)):
            args.append(wanted)
            where.append(f"kind = ANY(${len(args)}::text[])")
        if since is not None:
            args.append(since - LIVE_OVERLAP)
            where.append(f"stored_at > ${len(args)}")
        if after is not None:
            args += [after.time, after.trace_id, after.span_id, after.seq]
            n = len(args)
            where.append(
                f"(time, trace_id, span_id, seq) < (${n - 3}::timestamptz, ${n - 2}, ${n - 1},"
                f" ${n}::integer)"
            )
        args.append(limit + 1)
        sql = (
            f"SELECT *, now() AS as_of FROM guardrail_events{_where(where)}"  # noqa: S608
            f" ORDER BY time DESC, trace_id DESC, span_id DESC, seq DESC LIMIT ${len(args)}"
        )
        async with self._pool.acquire() as conn:
            rows: Sequence[asyncpg.Record] = await conn.fetch(sql, *args)
            as_of: datetime = rows[0]["as_of"] if rows else await conn.fetchval("SELECT now()")
        next_cursor = None
        if len(rows) > limit and since is None:
            last = rows[limit - 1]
            next_cursor = EventCursor(
                last["time"], last["trace_id"], last["span_id"], last["seq"], fingerprint
            ).encode()
        return GuardrailEventList(
            events=[_guardrail_event(r) for r in rows[:limit]], next_cursor=next_cursor, as_of=as_of
        )

    async def guardrail_summary(
        self, *, start: datetime | None, end: datetime | None, hide_sample: bool = False
    ) -> GuardrailSummary:
        """Blocks per rule (count of blocked calls) and redactions per kind (number of values
        replaced) for events with ``time`` in [start, end); by count descending, then name."""
        args: list[Any] = []
        where = _events_window(start, end, args, hide_sample)
        blocks = _where([*where, "kind = 'block'"])
        redactions = _where([*where, "kind = 'redaction'"])
        sql = (
            "SELECT 'block' AS k, coalesce(rule, 'unknown') AS v, count(*) AS n"  # noqa: S608
            f" FROM guardrail_events{blocks} GROUP BY 2"
            " UNION ALL SELECT 'redaction', redaction_kind, sum(count)"
            f" FROM guardrail_events{redactions} GROUP BY 2"
            " UNION ALL SELECT 'budget', 'budget', count(*)"
            f" FROM guardrail_events{_where([*where, "kind = 'budget'"])}"
        )
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
            as_of: datetime = await conn.fetchval("SELECT now()")
        ordered = sorted(rows, key=lambda r: (-int(r["n"]), r["v"]))
        return GuardrailSummary(
            blocks=[
                GuardrailRuleCount(rule=r["v"], blocks=int(r["n"]))
                for r in ordered
                if r["k"] == "block"
            ],
            redactions=[
                FacetValue(value=r["v"], count=int(r["n"]))
                for r in ordered
                if r["k"] == "redaction"
            ],
            budget_alerts=sum(int(r["n"]) for r in rows if r["k"] == "budget"),
            as_of=as_of,
        )

    # ------------------------------------------------------------------ M3: costs

    async def cost_series(
        self,
        *,
        start: datetime | None,
        end: datetime | None,
        group_by: CostGroup,
        hide_sample: bool = False,
    ) -> CostSeries:
        """Spend of spans with a cost and ``start_time`` in [start, end), per aligned time bucket
        (``date_bin`` from the Unix epoch, so UTC-aligned) and group (the span's model,
        ``lucentpad.client`` or ``service.name``; ``other`` when unset). The bucket size follows
        the window (``bucket_seconds_for``); an open start uses the oldest priced span, an open
        end the server's now."""
        args: list[Any] = []
        where = ["cost_usd IS NOT NULL", *(["NOT sample"] if hide_sample else [])]
        if start is not None:
            args.append(start)
            where.append(f"start_time >= ${len(args)}")
        if end is not None:
            args.append(end)
            where.append(f"start_time < ${len(args)}")
        async with self._pool.acquire() as conn:
            as_of: datetime = await conn.fetchval("SELECT now()")
            lo = start
            if lo is None:
                lo = await conn.fetchval(
                    f"SELECT min(start_time) FROM spans{_where(where)}",  # noqa: S608
                    *args,
                )
            hi = end if end is not None else as_of
            bucket = bucket_seconds_for((hi - lo) if lo is not None else timedelta(0))
            # Inlined (one of three ints) to match migration 0006's expression statistics.
            bin_sql = (
                f"date_bin('{int(bucket)} seconds'::interval, start_time, 'epoch'::timestamptz)"
            )
            group = _COST_GROUPS[group_by]
            rows = await conn.fetch(
                f"SELECT {bin_sql} AS bucket,"  # noqa: S608
                f" coalesce({group}, 'other') AS grp, sum(cost_usd) AS cost,"
                " coalesce(sum(input_tokens), 0) AS input_tokens,"
                " coalesce(sum(output_tokens), 0) AS output_tokens, count(*) AS calls"
                f" FROM spans{_where(where)} GROUP BY 1, 2"
                f" ORDER BY 1, coalesce({group}, 'other') COLLATE \"C\"",
                *args,
            )
        total = sum((r["cost"] for r in rows), Decimal(0))
        return CostSeries(
            points=[
                CostPoint(
                    bucket=r["bucket"],
                    group=r["grp"],
                    cost_usd=float(r["cost"]),
                    input_tokens=int(r["input_tokens"]),
                    output_tokens=int(r["output_tokens"]),
                    calls=int(r["calls"]),
                )
                for r in rows
            ],
            bucket_seconds=bucket,
            group_by=group_by,
            total_cost_usd=float(total),
            as_of=as_of,
        )

    # ------------------------------------------------------------------ M3: evals

    async def create_eval_run(self, run: EvalRunIn) -> EvalRun:
        """Store an eval run under a new server-generated id (16 hex characters)."""
        (run_id,) = await self.insert_eval_runs([run])
        return EvalRun(id=run_id, **run.model_dump())

    async def insert_eval_runs(
        self, runs: Sequence[EvalRunIn], *, sample: bool = False
    ) -> list[str]:
        """Store eval runs; returns their ids. Sample runs get ids derived from their suite and
        start time (stable demo links); others random ones."""
        ids: list[str] = []
        records: list[tuple[Any, ...]] = []
        for run in runs:
            run_id = _sample_run_id(run) if sample else secrets.token_hex(8)
            ids.append(run_id)
            passed = sum(1 for c in run.cases if c.passed)
            regressions = sum(1 for c in run.cases if not c.passed and c.baseline_passed)
            records.append(
                (
                    run_id,
                    run.suite,
                    run.status,
                    run.started_at,
                    run.duration_ms,
                    run.model,
                    run.git_sha,
                    run.git_ref,
                    run.ci_url,
                    run.cost_usd,
                    run.baseline_cost_usd,
                    passed,
                    len(run.cases) - passed,
                    regressions,
                    [c.model_dump(mode="json") for c in run.cases],
                    sample,
                )
            )
        if records:
            async with self._pool.acquire() as conn:
                await conn.executemany(
                    f"INSERT INTO eval_runs ({', '.join(_EVAL_COLUMNS)})"  # noqa: S608
                    f" VALUES ({', '.join(f'${i + 1}' for i in range(len(_EVAL_COLUMNS)))})"
                    " ON CONFLICT (id) DO NOTHING",
                    records,
                )
        return ids

    async def has_eval_runs(self) -> bool:
        async with self._pool.acquire() as conn:
            return bool(await conn.fetchval("SELECT EXISTS (SELECT 1 FROM eval_runs)"))

    async def is_sample_only(self) -> bool:
        """The startup sample was seeded and nothing real has been stored since (D11)."""
        async with self._pool.acquire() as conn:
            flags = {r["key"] for r in await conn.fetch("SELECT key FROM lucentpad_meta")}
        return META_SAMPLE_SEEDED in flags and META_REAL_DATA not in flags

    async def list_eval_runs(
        self, *, suite: str | None, limit: int, cursor: str | None, hide_sample: bool = False
    ) -> EvalRunList:
        """Eval runs (optionally of one suite), newest first by (started_at, id), with their
        passed / failed / regression counts. Raises ``InvalidCursorError`` for a cursor from
        another suite filter."""
        fingerprint = (suite or "") + ("|hide_sample" if hide_sample else "")
        after = EvalCursor.decode(cursor) if cursor is not None else None
        if after is not None and after.fingerprint != fingerprint:
            raise InvalidCursorError("cursor was issued for another suite")
        args: list[Any] = []
        where: list[str] = ["NOT sample"] if hide_sample else []
        if suite is not None:
            args.append(suite)
            where.append(f"suite = ${len(args)}")
        if after is not None:
            args += [after.started_at, after.run_id]
            where.append(f"(started_at, id) < (${len(args) - 1}::timestamptz, ${len(args)})")
        args.append(limit + 1)
        sql = (
            "SELECT id, suite, status, started_at, passed, failed, regressions, cost_usd,"  # noqa: S608
            f" baseline_cost_usd, git_sha, git_ref, ci_url FROM eval_runs{_where(where)}"
            f" ORDER BY started_at DESC, id DESC LIMIT ${len(args)}"
        )
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = EvalCursor(last["started_at"], last["id"], fingerprint).encode()
        return EvalRunList(
            runs=[EvalRunSummary.model_validate(dict(r)) for r in rows[:limit]],
            next_cursor=next_cursor,
        )

    async def get_eval_run(self, run_id: str) -> EvalRun | None:
        """One eval run with its case results, or None if unknown."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT id, {', '.join(_EVAL_IN_COLUMNS)} FROM eval_runs WHERE id = $1",  # noqa: S608
                run_id,
            )
        if row is None:
            return None
        return EvalRun.model_validate(dict(row))
