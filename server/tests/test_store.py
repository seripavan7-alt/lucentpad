from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import asyncpg
import pytest
import pytest_asyncio

from prism_server import db
from prism_server.schema import Attr, Span, SpanEvent
from prism_server.store import Cursor, InvalidCursorError, SpanStore

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
        assert [r["tablename"] for r in tables] == ["schema_migrations", "spans", "traces"]
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
    event = SpanEvent(name="prism.failover", time=NOW, attributes={"n": 1, "f": 0.5})
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
    assert detail.spans == sorted(spans, key=lambda s: s.start_time)
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
    assert t.cost_usd == pytest.approx(0.0045 + 0.00225 + 0.25)
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
    assert await store.list_traces(limit=10) == ([], None)


def test_cursor_roundtrip() -> None:
    c = Cursor(NOW, TRACE)
    assert Cursor.decode(c.encode()) == c


@pytest.mark.parametrize("token", ["", "not-base64!", "eyJ0IjogMX0", "bnVsbA"])
def test_cursor_invalid(token: str) -> None:
    with pytest.raises(InvalidCursorError):
        Cursor.decode(token)
