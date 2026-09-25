"""Ingest pipeline (M1 step 3): queue, batch writer, POST /v1/spans, stats, live `since`."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import asyncpg
import httpx
import pytest

from lucentpad_server import db
from lucentpad_server.app import create_app
from lucentpad_server.ingest.queue import InProcessSpanQueue, queue_max_from_env
from lucentpad_server.ingest.writer import QueuedIngest
from lucentpad_server.schema import (
    Attr,
    IngestStats,
    Span,
    TraceDetail,
    TraceFacets,
    TraceList,
)
from lucentpad_server.store import SpanStore

from .support import app_client

_counter = 0


def _ids() -> tuple[str, str]:
    global _counter
    _counter += 1
    return f"{_counter:032x}", f"{_counter:016x}"


def _trace(n_children: int = 2, **root_kw: Any) -> list[Span]:
    """A root plus ``n_children`` llm spans, starting a minute ago."""
    trace_id, root_id = _ids()
    start = datetime.now(UTC) - timedelta(minutes=1)
    root = Span.model_validate(
        {
            "trace_id": trace_id,
            "span_id": root_id,
            "name": "agent.run",
            "kind": "agent",
            "source": "sdk",
            "start_time": start,
            "end_time": start + timedelta(seconds=5),
            **root_kw,
        }
    )
    children = []
    for i in range(n_children):
        _, span_id = _ids()
        children.append(
            Span(
                trace_id=trace_id,
                span_id=span_id,
                parent_span_id=root_id,
                name="chat",
                kind="llm",
                source="sdk",
                start_time=start + timedelta(milliseconds=10 * (i + 1)),
                end_time=start + timedelta(milliseconds=10 * (i + 2)),
                attributes={
                    Attr.GEN_AI_REQUEST_MODEL: "claude-sonnet-5",
                    Attr.GEN_AI_INPUT_TOKENS: 100,
                    Attr.GEN_AI_OUTPUT_TOKENS: 10,
                },
            )
        )
    return [root, *children]


def _body(spans: Iterable[Span]) -> dict[str, Any]:
    return {"spans": [s.model_dump(mode="json") for s in spans]}


async def _stats(c: httpx.AsyncClient) -> IngestStats:
    r = await c.get("/v1/ingest/stats")
    assert r.status_code == 200
    return IngestStats.model_validate(r.json())


async def _settled(c: httpx.AsyncClient, wait: float = 5.0) -> IngestStats:
    """Stats once the queue is empty."""
    deadline = time.monotonic() + wait
    while True:
        stats = await _stats(c)
        if stats.queue_depth == 0 or time.monotonic() > deadline:
            return stats
        await asyncio.sleep(0.01)


def _pipeline(c: httpx.AsyncClient) -> QueuedIngest:
    transport = cast(httpx.ASGITransport, c._transport)
    return cast(QueuedIngest, transport.app.state.ingest)  # type: ignore[attr-defined]


def _client(db_url: str) -> AbstractAsyncContextManager[httpx.AsyncClient]:
    return app_client(create_app(db_url, seed_sample=False))


# --------------------------------------------------------------------------- queue


async def test_queue_all_or_nothing() -> None:
    q = InProcessSpanQueue(5)
    spans = _trace(2)
    assert q.offer(spans) and q.depth == 3
    assert not q.offer(spans)  # 3 + 3 > 5: nothing taken
    assert q.depth == 3
    assert q.offer(spans[:2]) and q.depth == 5
    assert await q.drain(4, 0) == [*spans, spans[0]]
    assert await q.drain(10, 0.01) == [spans[1]]
    assert await q.drain(10, 0.01) == []


async def test_queue_drain_returns_when_full_batch_arrives() -> None:
    q = InProcessSpanQueue(100)
    spans = _trace(3)

    async def feed() -> None:
        await asyncio.sleep(0.02)
        q.offer(spans)

    task = asyncio.create_task(feed())
    started = time.monotonic()
    assert await q.drain(4, 5.0) == spans
    assert time.monotonic() - started < 1.0
    await task


def test_queue_max_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LUCENTPAD_INGEST_QUEUE_MAX", raising=False)
    assert queue_max_from_env() == 50_000
    monkeypatch.setenv("LUCENTPAD_INGEST_QUEUE_MAX", "7")
    assert queue_max_from_env() == 7
    monkeypatch.setenv("LUCENTPAD_INGEST_QUEUE_MAX", "0")
    with pytest.raises(ValueError):
        queue_max_from_env()


# --------------------------------------------------------------------------- writer


class _FlakyStore:
    """Fails the first ``failures`` inserts, then records batches."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.batches: list[list[Span]] = []

    async def insert_spans(self, spans: Iterable[Span], *, sample: bool = False) -> int:
        if self.failures:
            self.failures -= 1
            raise OSError("connection refused")
        batch = list(spans)
        self.batches.append(batch)
        return len(batch)


def _writer(store: _FlakyStore, **kw: Any) -> QueuedIngest:
    return QueuedIngest(
        cast(SpanStore, store),
        InProcessSpanQueue(1000),
        batch_wait=0.01,
        retries=2,
        backoff=0.001,
        **kw,
    )


async def test_writer_retries_then_succeeds() -> None:
    store = _FlakyStore(failures=2)
    w = _writer(store)
    w.start()
    spans = _trace(1)
    assert w.offer(spans)
    await w.stop(2.0)
    assert store.batches == [spans]
    assert (w.stats().written_total, w.stats().write_errors_total) == (2, 0)


async def test_db_outage_counts_errors_and_keeps_running() -> None:
    store = _FlakyStore(failures=3)  # > retries: the first batch is dropped
    w = _writer(store)
    w.start()
    lost = _trace(1)
    assert w.offer(lost)
    for _ in range(200):
        if w.stats().write_errors_total:
            break
        await asyncio.sleep(0.01)
    assert w.stats().write_errors_total == 2
    later = _trace(2)
    assert w.offer(later)  # still alive and accepting
    await w.stop(2.0)
    assert store.batches == [later]
    stats = w.stats()
    assert (stats.accepted_total, stats.written_total, stats.queue_depth) == (5, 3, 0)


async def test_stop_refuses_new_spans() -> None:
    w = _writer(_FlakyStore(failures=0))
    w.start()
    await w.stop(1.0)
    assert not w.offer(_trace(0))
    assert w.stats().rejected_total == 1


# --------------------------------------------------------------------------- API


async def test_ingest_readable_within_500ms(db_url: str) -> None:
    async with _client(db_url) as c:
        spans = _trace(3)
        started = time.monotonic()
        r = await c.post("/v1/spans", json=_body(spans))
        assert r.status_code == 202
        assert r.json() == {"accepted": 4}
        while True:
            got = await c.get(f"/v1/traces/{spans[0].trace_id}")
            if got.status_code == 200 and got.json()["trace"]["span_count"] == 4:
                break
            await asyncio.sleep(0.005)
        elapsed = time.monotonic() - started
        print(f"ingest -> readable: {elapsed * 1000:.0f} ms")
        assert elapsed < 0.5
        listed = TraceList.model_validate((await c.get("/v1/traces")).json())
        assert [t.trace_id for t in listed.traces] == [spans[0].trace_id]


async def test_queue_full_429_and_nothing_accepted_is_lost(
    db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LUCENTPAD_INGEST_QUEUE_MAX", "30")
    async with _client(db_url) as c:
        too_big = _trace(30)  # 31 spans > capacity
        r = await c.post("/v1/spans", json=_body(too_big))
        assert r.status_code == 429
        assert r.headers["retry-after"] == "1"
        accepted: list[list[Span]] = []
        rejected: list[list[Span]] = []
        for _ in range(20):  # 20 x 10 spans, faster than the writer drains a 30-span queue
            spans = _trace(9)
            r = await c.post("/v1/spans", json=_body(spans))
            assert r.status_code in (202, 429)
            (accepted if r.status_code == 202 else rejected).append(spans)
        assert rejected, "the queue never filled"
        stats = await _settled(c)
        assert stats.queue_capacity == 30
        assert stats.accepted_total == 10 * len(accepted)
        assert stats.rejected_total == 31 + 10 * len(rejected)
        assert stats.written_total == stats.accepted_total and stats.write_errors_total == 0
        for spans in accepted:
            d = TraceDetail.model_validate((await c.get(f"/v1/traces/{spans[0].trace_id}")).json())
            assert d.trace.span_count == 10
        for spans in [too_big, *rejected]:
            assert (await c.get(f"/v1/traces/{spans[0].trace_id}")).status_code == 404


async def test_writer_batches_small_posts(db_url: str) -> None:
    async with _client(db_url) as c:
        for _ in range(50):
            r = await c.post("/v1/spans", json=_body(_trace(1)))
            assert r.status_code == 202
        stats = await _settled(c)
        assert stats.written_total == 100
        calls = _pipeline(c).insert_calls
        assert 1 <= calls <= 10, calls


async def test_duplicate_batches_do_not_double_count(db_url: str) -> None:
    async with _client(db_url) as c:
        spans = _trace(2)
        for _ in range(3):
            assert (await c.post("/v1/spans", json=_body(spans))).status_code == 202
        await _settled(c)
        d = TraceDetail.model_validate((await c.get(f"/v1/traces/{spans[0].trace_id}")).json())
        assert (d.trace.span_count, d.trace.input_tokens, d.trace.llm_calls) == (3, 200, 2)
        assert len(d.spans) == 3


async def test_shutdown_drains_queue(db_url: str) -> None:
    spans = _trace(5)
    async with _client(db_url) as c:
        assert (await c.post("/v1/spans", json=_body(spans))).status_code == 202
        # leave at once: the lifespan's shutdown must flush the queue
    pool = await db.create_pool(db_url)
    try:
        detail = await SpanStore(pool).get_trace(spans[0].trace_id)
    finally:
        await pool.close()
    assert detail is not None and detail.trace.span_count == 6


async def test_ingest_validates_before_queueing(db_url: str) -> None:
    async with _client(db_url) as c:
        bad = _body(_trace(1))
        bad["spans"][1]["kind"] = "nope"
        assert (await c.post("/v1/spans", json=bad)).status_code == 422
        stats = await _stats(c)
        assert (stats.accepted_total, stats.rejected_total) == (0, 0)


async def test_new_traces_are_filterable_and_faceted(db_url: str) -> None:
    async with _client(db_url) as c:
        spans = _trace(
            1,
            name="brand-new-run",
            attributes={Attr.SERVICE_NAME: "new-svc", Attr.CLIENT: "new-client"},
        )
        assert (await c.post("/v1/spans", json=_body(spans))).status_code == 202
        assert (await c.post("/v1/spans", json=_body(_trace(1)))).status_code == 202
        await _settled(c)
        for params in (
            {"name": "brand-new-run"},
            {"service": "new-svc"},
            {"client": "new-client", "model": "claude-sonnet-5"},
            {
                "from": (datetime.now(UTC) - timedelta(minutes=2)).isoformat(),
                "name": "brand-new-run",
            },
        ):
            page = TraceList.model_validate((await c.get("/v1/traces", params=params)).json())
            assert [t.trace_id for t in page.traces] == [spans[0].trace_id], params
        facets = TraceFacets.model_validate((await c.get("/v1/traces/facets")).json())
        assert {v.value: v.count for v in facets.name} == {"brand-new-run": 1, "agent.run": 1}
        assert [(v.value, v.count) for v in facets.model] == [("claude-sonnet-5", 2)]
        assert [(v.value, v.count) for v in facets.service] == [("new-svc", 1)]


# --------------------------------------------------------------------------- live `since`


async def _age_rows(db_url: str, by: timedelta) -> None:
    """Pretend everything stored so far was stored ``by`` ago."""
    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute("UPDATE traces SET updated_at = updated_at - $1::interval", by)
        await conn.execute("UPDATE spans SET stored_at = stored_at - $1::interval", by)
    finally:
        await conn.close()


async def test_list_since_returns_only_new_activity(db_url: str) -> None:
    async with _client(db_url) as c:
        old, touched = _trace(1), _trace(1)
        assert (await c.post("/v1/spans", json=_body([*old, touched[0]]))).status_code == 202
        await _settled(c)
        await _age_rows(db_url, timedelta(minutes=1))
        first = TraceList.model_validate((await c.get("/v1/traces")).json())
        assert len(first.traces) == 2
        new = _trace(1)
        # New activity: a brand-new trace and a new span on an existing trace.
        assert (await c.post("/v1/spans", json=_body([*new, touched[1]]))).status_code == 202
        await _settled(c)
        live = TraceList.model_validate(
            (await c.get("/v1/traces", params={"since": first.as_of.isoformat()})).json()
        )
        assert {t.trace_id for t in live.traces} == {new[0].trace_id, touched[0].trace_id}
        assert live.next_cursor is None and live.as_of > first.as_of
        quiet = await c.get("/v1/traces", params={"since": live.as_of.isoformat()})
        # Only the 2 s overlap can repeat traces; nothing older than that.
        assert {t["trace_id"] for t in quiet.json()["traces"]} <= {
            new[0].trace_id,
            touched[0].trace_id,
        }
        filtered = TraceList.model_validate(
            (
                await c.get("/v1/traces", params={"since": first.as_of.isoformat(), "limit": 1})
            ).json()
        )
        assert len(filtered.traces) == 1 and filtered.next_cursor is None


async def test_detail_since_returns_only_new_spans(db_url: str) -> None:
    async with _client(db_url) as c:
        spans = _trace(3)
        assert (await c.post("/v1/spans", json=_body(spans[:2]))).status_code == 202
        await _settled(c)
        await _age_rows(db_url, timedelta(minutes=1))
        url = f"/v1/traces/{spans[0].trace_id}"
        full = TraceDetail.model_validate((await c.get(url)).json())
        assert len(full.spans) == 2
        nothing = TraceDetail.model_validate(
            (await c.get(url, params={"since": full.as_of.isoformat()})).json()
        )
        assert nothing.spans == []
        assert (await c.post("/v1/spans", json=_body(spans[2:]))).status_code == 202
        await _settled(c)
        delta = TraceDetail.model_validate(
            (await c.get(url, params={"since": full.as_of.isoformat()})).json()
        )
        assert [s.span_id for s in delta.spans] == [s.span_id for s in spans[2:]]
        assert delta.trace.span_count == 4  # the summary is always the full current one
        assert delta.as_of >= full.as_of


# --------------------------------------------------------------------------- D11 via the lifespan


async def test_lifespan_shifts_sample_until_real_data(db_url: str) -> None:
    async with app_client(create_app(db_url, seed_sample=True)) as c:
        page = TraceList.model_validate((await c.get("/v1/traces", params={"limit": 1})).json())
        age = datetime.now(UTC) - page.traces[0].start_time
        assert timedelta(seconds=55) < age < timedelta(seconds=70)
        sample_newest = page.traces[0].trace_id
    await _age_starts(db_url, timedelta(hours=2))
    async with app_client(create_app(db_url, seed_sample=True)) as c:  # restart: shifted again
        page = TraceList.model_validate((await c.get("/v1/traces", params={"limit": 1})).json())
        assert page.traces[0].trace_id == sample_newest
        age = datetime.now(UTC) - page.traces[0].start_time
        assert timedelta(seconds=55) < age < timedelta(seconds=70)
        real = _trace(0, start_time=datetime.now(UTC) - timedelta(days=30))
        real = [real[0].model_copy(update={"end_time": real[0].start_time})]
        assert (await c.post("/v1/spans", json=_body(real))).status_code == 202
    await _age_starts(db_url, timedelta(hours=2))
    async with app_client(create_app(db_url, seed_sample=True)) as c:  # real data: no shift
        page = TraceList.model_validate((await c.get("/v1/traces", params={"limit": 1})).json())
        age = datetime.now(UTC) - page.traces[0].start_time
        assert timedelta(hours=2) < age < timedelta(hours=2, minutes=2)


async def _age_starts(db_url: str, by: timedelta) -> None:
    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute("UPDATE traces SET start_time = start_time - $1::interval", by)
    finally:
        await conn.close()
