from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg
import pytest
import pytest_asyncio

from lucentpad_server import db, sample
from lucentpad_server.query import TraceFilter
from lucentpad_server.schema import (
    FACET_MAX_VALUES,
    PREVIEW_MAX_CHARS,
    Attr,
    Span,
    SpanEvent,
    TraceOrder,
    TraceSort,
)
from lucentpad_server.store import Cursor, InvalidCursorError, SpanStore

from .support import NOW

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
ROOT = "00f067aa0ba902b7"


@pytest_asyncio.fixture
async def pool(db_url: str) -> AsyncIterator[asyncpg.Pool]:
    p = await db.create_pool(db_url)
    try:
        await db.migrate(p)
        yield p
    finally:
        await p.close()


def _span(span_id: str, parent: str | None = ROOT, offset_ms: int = 0, **kw: Any) -> Span:
    start = NOW - timedelta(minutes=5) + timedelta(milliseconds=offset_ms)
    fields: dict[str, Any] = {
        "trace_id": TRACE,
        "span_id": span_id,
        "parent_span_id": parent,
        "name": f"span {span_id}",
        "kind": "tool",
        "source": "sdk",
        "start_time": start,
        "end_time": start + timedelta(milliseconds=100),
    }
    fields.update(kw)
    return Span.model_validate(fields)


def _llm(span_id: str, offset_ms: int, model: str, in_tok: int, out_tok: int, **kw: Any) -> Span:
    attrs: dict[str, Any] = {
        **kw.pop("attributes", {}),
        Attr.GEN_AI_REQUEST_MODEL: model,
        Attr.GEN_AI_INPUT_TOKENS: in_tok,
        Attr.GEN_AI_OUTPUT_TOKENS: out_tok,
    }
    return _span(span_id, offset_ms=offset_ms, kind="llm", attributes=attrs, **kw)


async def test_migrations_idempotent(db_url: str) -> None:
    p = await db.create_pool(db_url)
    try:
        first = await db.migrate(p)
        assert first == [v for v, _ in db.migration_files()]
        assert await db.migrate(p) == []
        async with p.acquire() as conn:
            versions = await conn.fetch("SELECT version FROM schema_migrations ORDER BY version")
            tables = await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1"
            )
        assert [r["version"] for r in versions] == first
        assert [r["tablename"] for r in tables] == [
            "lucentpad_meta",
            "schema_migrations",
            "spans",
            "traces",
        ]
    finally:
        await p.close()


async def test_aggregates_and_roundtrip(pool: asyncpg.Pool) -> None:
    store = SpanStore(pool)
    root = _span(
        ROOT,
        parent=None,
        kind="agent",
        name="support-agent.run",
        attributes={Attr.SERVICE_NAME: "support-agent", Attr.CLIENT: "sdk"},
        end_time=NOW,
    )
    event = SpanEvent(name="lucentpad.failover", time=NOW, attributes={"n": 1, "f": 0.5})
    spans = [
        root,
        _llm("a000000000000001", 10, "claude-sonnet-5", 1000, 100, events=[event]),
        _span("a000000000000002", offset_ms=200, status="error", status_message="boom"),
        _llm("a000000000000003", 400, "claude-haiku-4-5", 2000, 50),
        # Explicit cost attribute wins over the price table; unknown model still listed.
        _llm("a000000000000004", 600, "mystery-model", 5, 5, attributes={Attr.COST_USD: 0.25}),
    ]
    assert await store.insert_spans(spans) == 5
    detail = await store.get_trace(TRACE)
    assert detail is not None
    # Priced llm spans come back with the server-computed cost; everything else unchanged.
    server_cost = {"a000000000000001": 0.003, "a000000000000003": 0.00225}
    expected = [
        s.model_copy(update={"attributes": {**s.attributes, Attr.COST_USD: server_cost[s.span_id]}})
        if s.span_id in server_cost
        else s
        for s in sorted(spans, key=lambda s: s.start_time)
    ]
    assert detail.spans == expected
    t = detail.trace
    assert (t.name, t.source, t.service_name, t.client) == (
        "support-agent.run",
        "sdk",
        "support-agent",
        "sdk",
    )
    assert t.status == "error"
    assert (t.span_count, t.llm_calls) == (5, 3)
    assert (t.input_tokens, t.output_tokens) == (3005, 155)
    assert t.cost_usd == pytest.approx(0.003 + 0.00225 + 0.25)
    assert t.models == ["claude-haiku-4-5", "claude-sonnet-5", "mystery-model"]
    assert t.duration_ms == pytest.approx(5 * 60 * 1000)


async def test_duplicates_ignored_and_root_arrives_last(pool: asyncpg.Pool) -> None:
    store = SpanStore(pool)
    child = _llm("a000000000000001", 10, "claude-sonnet-5", 1000, 100)
    blocked = _span("a000000000000002", offset_ms=20, kind="guardrail", status="blocked")
    assert await store.insert_spans([child, child]) == 1
    detail = await store.get_trace(TRACE)
    assert detail is not None
    assert detail.trace.name == child.name  # no root yet: earliest span stands in

    root = _span(ROOT, parent=None, kind="agent", name="root", end_time=NOW)
    assert await store.insert_spans([child, root, blocked]) == 2
    detail = await store.get_trace(TRACE)
    assert detail is not None
    assert detail.trace.name == "root"
    assert detail.trace.status == "blocked"
    assert (detail.trace.span_count, detail.trace.input_tokens) == (3, 1000)
    assert detail.trace.start_time == root.start_time


async def test_empty_and_unknown(pool: asyncpg.Pool) -> None:
    store = SpanStore(pool)
    assert await store.is_empty()
    assert await store.insert_spans([]) == 0
    assert await store.get_trace(TRACE) is None
    page = await store.list_traces(TraceFilter(), limit=10)
    assert (page.traces, page.next_cursor) == ([], None)


def test_cursor_roundtrip() -> None:
    c = Cursor(NOW, TRACE)
    assert Cursor.decode(c.encode()) == c
    for c in (
        Cursor(1234.567, TRACE, "duration", "asc", "fp"),
        Cursor(Decimal("0.01234567"), TRACE, "cost"),
        Cursor("support-agent.run", TRACE, "name", "asc"),
        Cursor("gateway", TRACE, "source"),
    ):
        assert Cursor.decode(c.encode()) == c


@pytest.mark.parametrize("token", ["", "not-base64!", "eyJ0IjogMX0", "bnVsbA"])
def test_cursor_invalid(token: str) -> None:
    with pytest.raises(InvalidCursorError):
        Cursor.decode(token)


@pytest.mark.parametrize(
    "fields",
    [
        {"s": "started", "k": "2026-09-25T12:00:00"},  # naive time
        {"s": "started", "k": 1.5},
        {"s": "duration", "k": "12.5"},
        {"s": "duration", "k": True},
        {"s": "duration", "k": float("nan")},
        {"s": "cost", "k": 0.5},
        {"s": "cost", "k": "abc"},
        {"s": "cost", "k": "Infinity"},
        {"s": "name", "k": 3},
        {"s": "colour", "k": "red"},
        {"s": "name", "k": "x", "o": "sideways"},
        {"k": "2026-09-25T12:00:00+00:00"},  # no sort
    ],
)
def test_cursor_key_must_match_sort(fields: dict[str, Any]) -> None:
    data = {"id": TRACE, "o": "desc", "f": "", **fields}
    token = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    with pytest.raises(InvalidCursorError):
        Cursor.decode(token)


# --------------------------------------------------------------------------- previews (D9)


def _previews(inp: str | None = None, out: str | None = None) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    if inp is not None:
        attrs[Attr.INPUT_PREVIEW] = inp
    if out is not None:
        attrs[Attr.OUTPUT_PREVIEW] = out
    return attrs


def _preview_trace() -> tuple[Span, list[Span]]:
    root = _span(
        ROOT, parent=None, kind="agent", end_time=NOW, attributes=_previews("question", "answer")
    )
    children = [
        _llm("a000000000000001", 10, "claude-sonnet-5", 1, 1, attributes=_previews("in1", "out1")),
        # A tool span's previews never count.
        _span("a000000000000002", offset_ms=20, attributes=_previews("tool-in", "tool-out")),
        _llm("a000000000000003", 30, "claude-sonnet-5", 1, 1, attributes=_previews("in3", "out3")),
        _llm("a000000000000004", 40, "claude-sonnet-5", 1, 1, attributes=_previews("in4", None)),
    ]
    return root, children


async def _preview_pair(store: SpanStore) -> tuple[str | None, str | None]:
    detail = await store.get_trace(TRACE)
    assert detail is not None
    return detail.trace.input_preview, detail.trace.output_preview


async def test_previews_root_first(pool: asyncpg.Pool) -> None:
    store = SpanStore(pool)
    root, children = _preview_trace()
    await store.insert_spans([root])
    assert await _preview_pair(store) == ("question", "answer")
    for child in reversed(children):
        await store.insert_spans([child])
    assert await _preview_pair(store) == ("question", "answer")


async def test_previews_root_last(pool: asyncpg.Pool) -> None:
    store = SpanStore(pool)
    root, children = _preview_trace()
    # Out of order: the earliest llm input and the latest llm output win until the root.
    await store.insert_spans([children[2]])
    assert await _preview_pair(store) == ("in3", "out3")
    await store.insert_spans([children[3], children[1]])
    assert await _preview_pair(store) == ("in3", "out3")  # span 4 has no output preview
    await store.insert_spans([children[0]])
    assert await _preview_pair(store) == ("in1", "out3")
    await store.insert_spans([root])
    assert await _preview_pair(store) == ("question", "answer")


async def test_previews_without_root(pool: asyncpg.Pool) -> None:
    store = SpanStore(pool)
    _, children = _preview_trace()
    await store.insert_spans(children)  # one batch
    assert await _preview_pair(store) == ("in1", "out3")


async def test_previews_root_without_preview_keeps_llm_ones(pool: asyncpg.Pool) -> None:
    store = SpanStore(pool)
    _, children = _preview_trace()
    await store.insert_spans(children)
    await store.insert_spans([_span(ROOT, parent=None, kind="agent", end_time=NOW)])
    assert await _preview_pair(store) == ("in1", "out3")


async def test_no_previews_is_null(pool: asyncpg.Pool) -> None:
    store = SpanStore(pool)
    await store.insert_spans([_llm("a000000000000001", 10, "claude-sonnet-5", 1, 1)])
    assert await _preview_pair(store) == (None, None)


async def test_preview_capped_server_side(pool: asyncpg.Pool) -> None:
    store = SpanStore(pool)
    long = "x" * (PREVIEW_MAX_CHARS + 50)
    await store.insert_spans(
        [_span(ROOT, parent=None, kind="agent", attributes=_previews(long, "short"))]
    )
    detail = await store.get_trace(TRACE)
    assert detail is not None
    assert detail.trace.input_preview == "x" * PREVIEW_MAX_CHARS
    assert detail.trace.output_preview == "short"
    attrs = detail.spans[0].attributes
    assert attrs[Attr.INPUT_PREVIEW] == "x" * PREVIEW_MAX_CHARS
    assert attrs[Attr.INPUT_TRUNCATED] is True
    assert Attr.OUTPUT_TRUNCATED not in attrs


# --------------------------------------------------------------------------- facets


async def test_facet_values_capped_and_sorted(pool: asyncpg.Pool) -> None:
    store = SpanStore(pool)
    spans = []
    for i in range(FACET_MAX_VALUES + 10):
        # Trace i has name n{i:02d}; names < 5 appear twice.
        for copy in range(2 if i < 5 else 1):
            spans.append(
                _span(
                    f"{i * 2 + copy + 1:016x}",
                    parent=None,
                    name=f"n{i:02d}",
                    trace_id=f"{i * 2 + copy + 1:032x}",
                )
            )
    await store.insert_spans(spans)
    facets = await store.facets(TraceFilter())
    assert len(facets.name) == FACET_MAX_VALUES
    assert [(v.value, v.count) for v in facets.name[:6]] == [
        ("n00", 2),
        ("n01", 2),
        ("n02", 2),
        ("n03", 2),
        ("n04", 2),
        ("n05", 1),
    ]
    assert facets.name[-1].value == f"n{FACET_MAX_VALUES - 1:02d}"
    assert facets.client == [] and facets.model == []  # nulls / no models aren't values


# --------------------------------------------------------------------------- D11 sample shift


async def _newest_start(pool: asyncpg.Pool) -> timedelta:
    async with pool.acquire() as conn:
        age: timedelta = await conn.fetchval("SELECT now() - max(start_time) FROM traces")
    return age


async def test_sample_shift(pool: asyncpg.Pool) -> None:
    store = SpanStore(pool)
    spans = sample.generate(datetime.now(UTC) - timedelta(days=3))
    await store.insert_spans(spans, sample=True)
    with_event = next(s for s in spans if s.events)
    delta = await store.shift_sample_to_now()
    # Expected: move the newest trace start to 60 s ago. (Not simply ~3 days: the sample favours
    # working hours, so its newest trace can start well before the generation time.)
    trace_starts: dict[str, datetime] = {}
    for s in spans:
        trace_starts[s.trace_id] = min(trace_starts.get(s.trace_id, s.start_time), s.start_time)
    expected = datetime.now(UTC) - timedelta(seconds=60) - max(trace_starts.values())
    assert delta is not None and abs(delta - expected) < timedelta(seconds=5)
    assert timedelta(seconds=59) < await _newest_start(pool) < timedelta(seconds=65)
    detail = await store.get_trace(with_event.trace_id)
    assert detail is not None
    moved = next(s for s in detail.spans if s.span_id == with_event.span_id)
    assert moved.start_time - with_event.start_time == delta
    assert moved.end_time - with_event.end_time == delta
    assert [e.time - e0.time for e, e0 in zip(moved.events, with_event.events, strict=True)] == [
        delta
    ] * len(with_event.events)
    assert detail.trace.start_time == min(s.start_time for s in detail.spans)
    # Idempotent: straight away there is nothing (left) to shift.
    assert await store.shift_sample_to_now() is None
    assert timedelta(seconds=59) < await _newest_start(pool) < timedelta(seconds=65)


async def _age_everything(pool: asyncpg.Pool, by: timedelta) -> None:
    async with pool.acquire() as conn:
        await conn.execute("UPDATE traces SET start_time = start_time - $1::interval", by)


async def test_sample_shift_repeats_on_restart(pool: asyncpg.Pool) -> None:
    store = SpanStore(pool)
    await store.insert_spans(sample.generate(datetime.now(UTC)), sample=True)
    assert await store.shift_sample_to_now() is not None
    await _age_everything(pool, timedelta(hours=5))  # time passes while the API is down
    later = SpanStore(pool)  # a restarted API
    delta = await later.shift_sample_to_now()
    assert delta is not None and abs(delta - timedelta(hours=5)) < timedelta(seconds=5)
    assert timedelta(seconds=59) < await _newest_start(pool) < timedelta(seconds=65)


async def test_sample_shift_skipped_once_real_data_exists(pool: asyncpg.Pool) -> None:
    store = SpanStore(pool)
    await store.insert_spans(sample.generate(datetime.now(UTC) - timedelta(days=1)), sample=True)
    await store.insert_spans([_span(ROOT, parent=None, start_time=NOW - timedelta(days=30))])
    before = await _newest_start(pool)
    assert before > timedelta(hours=23)
    assert await store.shift_sample_to_now() is None
    assert await _newest_start(pool) - before < timedelta(seconds=1)  # nothing moved
    await _age_everything(pool, timedelta(hours=1))
    assert await SpanStore(pool).shift_sample_to_now() is None


async def test_no_shift_without_sample_flag(pool: asyncpg.Pool) -> None:
    store = SpanStore(pool)
    assert await store.shift_sample_to_now() is None  # empty
    await store.insert_spans(sample.generate(datetime.now(UTC) - timedelta(days=1)))
    assert await store.shift_sample_to_now() is None  # inserted as real data


# --------------------------------------------------------------------------- sorts


def _sort_fixture() -> list[Span]:
    """36 single-span traces with many ties on every sort key; names that differ between byte
    order and a linguistic collation ("Zeta" < "alpha" < "Émile" in code point order)."""
    names = ["alpha", "Zeta", "Émile", "_tool", "alpha", "beta"]
    spans = []
    for i in range(36):
        start = NOW - timedelta(minutes=i % 5)
        spans.append(
            Span.model_validate(
                {
                    "trace_id": f"{(i * 7919) % 997 + 1:032x}",
                    "span_id": f"{i + 1:016x}",
                    "name": names[i % len(names)],
                    "kind": "agent",
                    "source": "sdk" if i % 3 else "gateway",
                    "start_time": start,
                    "end_time": start + timedelta(milliseconds=100 * (i % 4) + 0.5),
                    "status": "error" if i % 4 == 0 else "ok",
                    "attributes": {Attr.COST_USD: [0.0, 0.00012345, 1.5][i % 3]},
                }
            )
        )
    return spans


_SORT_KEYS: dict[str, Any] = {
    "started": lambda t: t.start_time,
    "duration": lambda t: t.duration_ms,
    "name": lambda t: t.name.encode(),  # UTF-8 byte order == code point order
    "source": lambda t: t.source,
    "cost": lambda t: t.cost_usd,
}


@pytest.mark.parametrize("order", ["desc", "asc"])
@pytest.mark.parametrize("sort", list(_SORT_KEYS))
@pytest.mark.parametrize(
    "filters",
    [
        TraceFilter(),
        TraceFilter(status=("error",)),
        TraceFilter(start=NOW - timedelta(minutes=3), end=NOW, source=("sdk",)),
    ],
)
async def test_sort_pages_every_trace_once(
    pool: asyncpg.Pool, sort: Any, order: Any, filters: TraceFilter
) -> None:
    store = SpanStore(pool)
    await store.insert_spans(_sort_fixture())
    everything = (await store.list_traces(TraceFilter(), limit=200)).traces
    assert len(everything) == 36
    matching = [
        t
        for t in everything
        if (filters.start is None or t.start_time >= filters.start)
        and (filters.end is None or t.start_time < filters.end)
        and (not filters.status or t.status in filters.status)
        and (not filters.source or t.source in filters.source)
    ]
    key = _SORT_KEYS[sort]
    expected = sorted(matching, key=lambda t: (key(t), t.trace_id), reverse=order == "desc")
    got: list[str] = []
    cursor: str | None = None
    for _ in range(100):
        page = await store.list_traces(filters, limit=4, cursor=cursor, sort=sort, order=order)
        assert len(page.traces) <= 4
        got += [t.trace_id for t in page.traces]
        cursor = page.next_cursor
        if cursor is None:
            break
    assert got == [t.trace_id for t in expected]
    # Every sort has ties, so trace_id really is the tie-breaker.
    assert len({key(t) for t in matching}) < len(matching)


async def test_sort_cursor_bound_to_sort(pool: asyncpg.Pool) -> None:
    store = SpanStore(pool)
    await store.insert_spans(_sort_fixture())
    page = await store.list_traces(TraceFilter(), limit=3, sort="cost")
    assert page.next_cursor is not None
    combos: list[tuple[TraceSort, TraceOrder]] = [
        ("started", "desc"),
        ("duration", "desc"),
        ("cost", "asc"),
    ]
    for sort, order in combos:
        with pytest.raises(InvalidCursorError):
            await store.list_traces(
                TraceFilter(), limit=3, cursor=page.next_cursor, sort=sort, order=order
            )


async def test_sort_with_since(pool: asyncpg.Pool) -> None:
    store = SpanStore(pool)
    await store.insert_spans(_sort_fixture())
    since = datetime.now(UTC) - timedelta(hours=1)
    live = await store.list_traces(
        TraceFilter(), limit=200, sort="duration", order="asc", since=since
    )
    assert live.next_cursor is None and len(live.traces) == 36
    keys = [(t.duration_ms, t.trace_id) for t in live.traces]
    assert keys == sorted(keys)
    capped = await store.list_traces(TraceFilter(), limit=5, sort="name", since=since)
    assert capped.next_cursor is None
    assert [t.trace_id for t in capped.traces] == [
        t.trace_id
        for t in sorted(live.traces, key=lambda t: (t.name.encode(), t.trace_id))[::-1][:5]
    ]
