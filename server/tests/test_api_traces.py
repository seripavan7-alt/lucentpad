from __future__ import annotations

from collections import Counter
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio

from lucentpad_server import db, sample
from lucentpad_server.app import create_app
from lucentpad_server.schema import Span, TraceDetail, TraceList, TraceSummary
from lucentpad_server.store import SpanStore

from .support import NOW, app_client

SPANS = sample.generate(NOW)
TRACE_IDS = {s.trace_id for s in SPANS}


@pytest_asyncio.fixture(scope="module")
async def client(module_db_url: str) -> AsyncIterator[httpx.AsyncClient]:
    """App over a database holding the deterministic sample (inserted once per module)."""
    pool = await db.create_pool(module_db_url)
    try:
        await db.migrate(pool)
        await SpanStore(pool).insert_spans(SPANS)
    finally:
        await pool.close()
    async with app_client(create_app(module_db_url, seed_sample=False)) as c:
        yield c


async def _all_pages(client: httpx.AsyncClient, **params: Any) -> list[TraceSummary]:
    out: list[TraceSummary] = []
    cursor: str | None = None
    for _ in range(1000):
        query = {**params, **({"cursor": cursor} if cursor else {})}
        r = await client.get("/v1/traces", params=query)
        assert r.status_code == 200, r.text
        page = TraceList.model_validate(r.json())
        out += page.traces
        if page.next_cursor is None:
            return out
        cursor = page.next_cursor
    raise AssertionError("pagination did not terminate")


async def test_default_page(client: httpx.AsyncClient) -> None:
    r = await client.get("/v1/traces")
    assert r.status_code == 200
    page = TraceList.model_validate(r.json())
    assert len(page.traces) == 50
    assert page.next_cursor is not None


async def test_pagination_walks_everything_in_order(client: httpx.AsyncClient) -> None:
    traces = await _all_pages(client, limit=37)
    assert [t.trace_id for t in traces] and {t.trace_id for t in traces} == TRACE_IDS
    assert len(traces) == len(TRACE_IDS)
    keys = [(t.start_time, t.trace_id) for t in traces]
    assert keys == sorted(keys, reverse=True)


async def test_exact_page_boundary(client: httpx.AsyncClient) -> None:
    r = await client.get("/v1/traces", params={"limit": 200})
    first = TraceList.model_validate(r.json())
    assert first.next_cursor is not None
    r = await client.get("/v1/traces", params={"limit": 200, "cursor": first.next_cursor})
    second = TraceList.model_validate(r.json())
    assert len(first.traces) + len(second.traces) == len(TRACE_IDS)
    assert second.next_cursor is None


@pytest.mark.parametrize(
    ("params", "field", "value"),
    [
        ({"source": "gateway"}, "source", "gateway"),
        ({"source": "sdk"}, "source", "sdk"),
        ({"status": "blocked"}, "status", "blocked"),
        ({"status": "error"}, "status", "error"),
        ({"status": "ok", "source": "sdk"}, "source", "sdk"),
    ],
)
async def test_filters(
    client: httpx.AsyncClient, params: dict[str, str], field: str, value: str
) -> None:
    traces = await _all_pages(client, limit=25, **params)
    assert traces
    assert all(getattr(t, field) == value for t in traces)
    for key, expected in params.items():
        assert all(getattr(t, key) == expected for t in traces)


async def test_filter_counts_add_up(client: httpx.AsyncClient) -> None:
    by_status = Counter[str]()
    for st in ("ok", "error", "blocked"):
        by_status[st] = len(await _all_pages(client, limit=200, status=st))
    assert sum(by_status.values()) == len(TRACE_IDS)
    assert by_status["blocked"] >= 1


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 201},
        {"source": "cli"},
        {"status": "warning"},
        {"cursor": "garbage!!"},
        {"cursor": "eyJ0IjogMX0"},
    ],
)
async def test_bad_query_params(client: httpx.AsyncClient, params: dict[str, Any]) -> None:
    r = await client.get("/v1/traces", params=params)
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], list)


async def test_trace_detail(client: httpx.AsyncClient) -> None:
    guardrail = next(s for s in SPANS if s.kind == "guardrail")
    r = await client.get(f"/v1/traces/{guardrail.trace_id}")
    assert r.status_code == 200
    detail = TraceDetail.model_validate(r.json())
    expected = sorted(
        (s for s in SPANS if s.trace_id == guardrail.trace_id),
        key=lambda s: (s.start_time, s.span_id),
    )
    assert detail.spans == expected
    t = detail.trace
    assert t.status == "blocked"
    assert t.span_count == len(expected)
    assert t.llm_calls == sum(s.kind == "llm" for s in expected)
    root = next(s for s in expected if s.parent_span_id is None)
    assert t.name == root.name and t.service_name == "support-agent" and t.client == "sdk"
    assert t.start_time == root.start_time
    assert t.duration_ms == pytest.approx(
        (max(s.end_time for s in expected) - root.start_time) / timedelta(milliseconds=1)
    )


async def test_gateway_trace_summary(client: httpx.AsyncClient) -> None:
    traces = await _all_pages(client, limit=200, source="gateway")
    assert {t.client for t in traces} == {"claude-code", "copilot-chat", "copilot-cli"}
    assert all(t.service_name == "lucentpad-gateway" and t.llm_calls >= 1 for t in traces)
    assert all(t.cost_usd > 0 and t.input_tokens > 0 for t in traces)


@pytest.mark.parametrize(
    "trace_id",
    [
        "4bf92f3577b34da6a3ce929d0e0e4736",  # well-formed but unknown
        "0" * 32,
        "4BF92F3577B34DA6A3CE929D0E0E4736",
        "nope",
    ],
)
async def test_trace_not_found(client: httpx.AsyncClient, trace_id: str) -> None:
    r = await client.get(f"/v1/traces/{trace_id}")
    assert r.status_code == 404
    assert r.json() == {"detail": "trace not found"}


async def test_lifespan_seeds_empty_database_once(db_url: str) -> None:
    async with app_client(create_app(db_url, seed_sample=True)) as c:
        first = await _all_pages(c, limit=200)
    assert 150 <= len(first) <= 300
    async with app_client(create_app(db_url, seed_sample=True)) as c:
        again = await _all_pages(c, limit=200)
    assert len(again) == len(first)  # not seeded twice


async def test_lifespan_without_seed(db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.delenv("LUCENTPAD_SEED_SAMPLE", raising=False)
    async with app_client(create_app()) as c:
        r = await c.get("/v1/traces")
    assert r.json() == {"traces": [], "next_cursor": None}


async def test_lifespan_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        async with app_client(create_app()):
            pass


def test_sample_spans_are_spans() -> None:
    assert all(isinstance(s, Span) for s in SPANS)
