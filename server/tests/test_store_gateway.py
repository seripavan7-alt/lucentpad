"""Gateway turn fields and live polling at the store level, over hand-built spans."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import asyncpg
import pytest
import pytest_asyncio

from lucentpad_server import db
from lucentpad_server.schema import Attr, EventName, Span, SpanEvent
from lucentpad_server.store import GatewayCursor, InvalidCursorError, SpanStore

from .support import NOW

TRACE_A = "a" * 31 + "1"
TRACE_B = "b" * 31 + "2"
T0 = NOW - timedelta(hours=1)


@pytest_asyncio.fixture
async def pool(db_url: str) -> AsyncIterator[asyncpg.Pool]:
    p = await db.create_pool(db_url)
    try:
        await db.migrate(p)
        yield p
    finally:
        await p.close()


def _span(trace: str, span_id: str, offset_s: float, **kw: Any) -> Span:
    start = T0 + timedelta(seconds=offset_s)
    fields: dict[str, Any] = {
        "trace_id": trace,
        "span_id": span_id,
        "parent_span_id": None,
        "name": "turn",
        "kind": "llm",
        "source": "gateway",
        "start_time": start,
        "end_time": start + timedelta(milliseconds=1500),
    }
    fields.update(kw)
    return Span.model_validate(fields)


def _turn(trace: str, span_id: str, offset_s: float, **attrs: Any) -> Span:
    base: dict[str, Any] = {
        Attr.CLIENT: "claude-code",
        Attr.GEN_AI_SYSTEM: "anthropic",
        Attr.GEN_AI_REQUEST_MODEL: "claude-sonnet-5",
        Attr.GEN_AI_INPUT_TOKENS: 100,
        Attr.GEN_AI_OUTPUT_TOKENS: 20,
    }
    base.update(attrs)
    base = {k: v for k, v in base.items() if v is not None}  # None: leave the attribute out
    return _span(trace, span_id, offset_s, parent_span_id="f" * 16, attributes=base)


async def test_turn_fields(pool: asyncpg.Pool) -> None:
    failover = SpanEvent(
        name=EventName.FAILOVER, time=T0, attributes={Attr.FAILOVER_STATUS_CODE: 429}
    )
    spans = [
        _span(TRACE_A, "f" * 16, 0, kind="agent", attributes={Attr.CLIENT: "claude-code"}),
        _turn(
            TRACE_A,
            "1" * 16,
            1,
            **{
                Attr.GATEWAY_UPSTREAM: "openai",
                Attr.GEN_AI_RESPONSE_MODEL: "claude-sonnet-5-20260901",
                Attr.TTFB_MS: 412.5,
                Attr.COST_USD: 0.25,  # unpriced model: the producer's cost is kept
                Attr.STREAMING: True,
                Attr.INPUT_PREVIEW: "hi",
                Attr.OUTPUT_PREVIEW: "hello",
            },
        ).model_copy(update={"events": [failover], "status": "error"}),
        _turn(TRACE_A, "2" * 16, 2, **{Attr.TTFB_MS: 90, Attr.STREAMING: False}),
        _turn(
            TRACE_B,
            "3" * 16,
            3,
            **{Attr.CLIENT: None, Attr.GEN_AI_SYSTEM: "openai", Attr.GEN_AI_REQUEST_MODEL: "x"},
        ),
        _turn(TRACE_B, "4" * 16, 4, **{Attr.GEN_AI_SYSTEM: "vertex"}),
        _turn(TRACE_B, "5" * 16, 5).model_copy(update={"source": "sdk"}),  # not gateway
    ]
    store = SpanStore(pool)
    await store.insert_spans(spans)
    page = await store.gateway_turns(
        start=None, end=None, clients=(), limit=50, cursor=None, since=None
    )
    turns = {t.span_id: t for t in page.turns}
    assert [t.span_id for t in page.turns] == ["4" * 16, "3" * 16, "2" * 16, "1" * 16]
    one, two, three, four = (turns[c * 16] for c in "1234")
    assert (one.provider, one.model, one.ttfb_ms, one.streaming) == (
        "openai",  # the upstream attribute wins over gen_ai.system
        "claude-sonnet-5-20260901",  # response model over request model
        412.5,
        True,
    )
    assert one.failover and one.status == "error"
    assert (one.input_preview, one.output_preview) == ("hi", "hello")
    assert one.duration_ms == pytest.approx(1500)
    assert one.cost_usd == 0.25
    assert (two.provider, two.model, two.ttfb_ms, two.streaming, two.failover) == (
        "anthropic",
        "claude-sonnet-5",
        90.0,
        False,
        False,
    )
    assert (two.input_preview, two.output_preview) == (None, None)
    assert (three.client, three.provider, three.cost_usd) == (None, "openai", None)
    assert four.provider is None

    summary = await store.gateway_summary(start=None, end=None)
    rows = {c.client: c for c in summary.clients}
    assert set(rows) == {"claude-code", "other"}
    cc = rows["claude-code"]
    assert (cc.sessions, cc.turns, cc.input_tokens, cc.output_tokens) == (2, 3, 300, 60)
    assert summary.clients[0].client == "claude-code"  # most expensive first
    assert (rows["other"].turns, rows["other"].cost_usd) == (1, 0.0)

    windowed = await store.gateway_summary(
        start=T0 + timedelta(seconds=2), end=T0 + timedelta(seconds=4)
    )
    assert [(c.client, c.turns, c.sessions) for c in windowed.clients] == [
        ("claude-code", 1, 1),
        ("other", 1, 1),
    ]


async def test_same_start_time_pages_by_span_then_trace(pool: asyncpg.Pool) -> None:
    store = SpanStore(pool)
    spans = [_turn(t, s, 0) for t in (TRACE_A, TRACE_B) for s in ("1" * 16, "2" * 16)]
    await store.insert_spans(spans)
    seen: list[tuple[str, str]] = []
    cursor: str | None = None
    while True:
        page = await store.gateway_turns(
            start=None, end=None, clients=(), limit=1, cursor=cursor, since=None
        )
        seen += [(t.span_id, t.trace_id) for t in page.turns]
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    assert seen == sorted(((s.span_id, s.trace_id) for s in spans), reverse=True)


async def test_since_only_recent_stores(pool: asyncpg.Pool) -> None:
    store = SpanStore(pool)
    await store.insert_spans([_turn(TRACE_A, c * 16, i) for i, c in enumerate("123")])
    async with pool.acquire() as conn:
        await conn.execute("UPDATE spans SET stored_at = now() - interval '1 hour'")
        cutoff = await conn.fetchval("SELECT now() - interval '10 minutes'")
    await store.insert_spans([_turn(TRACE_B, c * 16, 10 + i) for i, c in enumerate("456")])
    page = await store.gateway_turns(
        start=None, end=None, clients=(), limit=2, cursor=None, since=cutoff
    )
    assert [t.span_id for t in page.turns] == ["6" * 16, "5" * 16]
    assert page.next_cursor is None
    everything = await store.gateway_turns(
        start=None, end=None, clients=(), limit=10, cursor=None, since=cutoff
    )
    assert {t.trace_id for t in everything.turns} == {TRACE_B}


async def test_cursor_round_trip_and_rejects() -> None:
    c = GatewayCursor(NOW, "1" * 16, TRACE_A, "abcd")
    assert GatewayCursor.decode(c.encode()) == c
    for bad in ("", "!!", "e30", "eyJnIjoyfQ"):
        with pytest.raises(InvalidCursorError):
            GatewayCursor.decode(bad)
