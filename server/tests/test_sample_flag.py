"""The sample label: sample data is tagged and can be hidden; /v1/data says what's there."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest_asyncio

from lucentpad_server import db, sample
from lucentpad_server.app import create_app
from lucentpad_server.schema import Attr, Span

from .support import NOW, app_client

REAL_TRACE = "feedfacefeedfacefeedfacefeedface"


def _real_gateway_run() -> list[Span]:
    """One real Claude Code turn through the gateway (root + llm span)."""
    start = NOW - timedelta(minutes=5)
    common = {Attr.CLIENT: "claude-code", Attr.SERVICE_NAME: "lucentpad-gateway"}
    root = Span(
        trace_id=REAL_TRACE,
        span_id="aaaaaaaaaaaaaaaa",
        name="claude-code session",
        kind="agent",
        source="gateway",
        start_time=start,
        end_time=start,
        attributes=common,
    )
    turn = Span(
        trace_id=REAL_TRACE,
        span_id="bbbbbbbbbbbbbbbb",
        parent_span_id="aaaaaaaaaaaaaaaa",
        name="messages claude-haiku-4-5",
        kind="llm",
        source="gateway",
        start_time=start,
        end_time=start + timedelta(seconds=2),
        attributes={
            **common,
            Attr.GEN_AI_RESPONSE_MODEL: "claude-haiku-4-5",
            Attr.GEN_AI_INPUT_TOKENS: 100,
            Attr.GEN_AI_OUTPUT_TOKENS: 10,
        },
    )
    return [root, turn]


@pytest_asyncio.fixture
async def client(db_url: str) -> AsyncIterator[httpx.AsyncClient]:
    """Sample data seeded like a first `make dev`; no real data yet."""
    from lucentpad_server.store import SpanStore

    pool = await db.create_pool(db_url)
    try:
        await db.migrate(pool)
        await SpanStore(pool).insert_spans(sample.generate(NOW), sample=True)
        await pool.execute("INSERT INTO lucentpad_meta (key, value) VALUES ('real_data_at', 'x')")
    finally:
        await pool.close()
    async with app_client(create_app(db_url, seed_sample=False)) as c:
        yield c


async def _ingest_real(client: httpx.AsyncClient) -> None:
    body = {"spans": [s.model_dump(mode="json") for s in _real_gateway_run()]}
    assert (await client.post("/v1/spans", json=body)).status_code == 202
    for _ in range(100):
        r = await client.get(f"/v1/traces/{REAL_TRACE}")
        if r.status_code == 200 and len(r.json()["spans"]) == 2:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("real spans not written")


async def test_sample_only_then_mixed(client: httpx.AsyncClient) -> None:
    window: dict[str, str | int | bool] = {
        "from": (NOW - timedelta(days=30)).isoformat(),
        "limit": 200,
    }
    assert (await client.get("/v1/data")).json() == {"sample_data": True, "real_data": False}
    listed = (await client.get("/v1/traces", params=window)).json()["traces"]
    assert listed and all(t["sample"] for t in listed)

    await _ingest_real(client)
    assert (await client.get("/v1/data")).json() == {"sample_data": True, "real_data": True}

    both = (await client.get("/v1/traces", params=window)).json()["traces"]
    real = [t for t in both if not t["sample"]]
    assert [t["trace_id"] for t in real] == [REAL_TRACE]

    hidden = (await client.get("/v1/traces", params={**window, "hide_sample": True})).json()
    assert [t["trace_id"] for t in hidden["traces"]] == [REAL_TRACE]
    facets = (await client.get("/v1/traces/facets", params={**window, "hide_sample": True})).json()
    assert facets["client"] == [{"value": "claude-code", "count": 1}]

    turns = (await client.get("/v1/gateway/turns", params={**window, "hide_sample": True})).json()
    assert [t["trace_id"] for t in turns["turns"]] == [REAL_TRACE]
    summary = (
        await client.get(
            "/v1/gateway/summary", params={"hide_sample": True, "from": window["from"]}
        )
    ).json()
    assert [(c["client"], c["turns"]) for c in summary["clients"]] == [("claude-code", 1)]


async def test_cursor_is_bound_to_hide_sample(client: httpx.AsyncClient) -> None:
    first = (await client.get("/v1/traces", params={"limit": 5})).json()
    r = await client.get(
        "/v1/traces", params={"limit": 5, "cursor": first["next_cursor"], "hide_sample": True}
    )
    assert r.status_code == 422


async def test_migration_labels_an_existing_sample(db_url: str) -> None:
    """A database seeded before the sample column existed gets its sample labelled."""
    from lucentpad_server.store import SpanStore

    pool = await db.create_pool(db_url)
    try:
        await db.migrate(pool)
        await SpanStore(pool).insert_spans(sample.generate(NOW), sample=True)
        await pool.execute("UPDATE spans SET sample = false; UPDATE traces SET sample = false")
        # Re-run the backfill part of migration 0005 against the old-style data.
        from importlib import resources

        path = resources.files("lucentpad_server") / "migrations" / "0005_sample_flag.sql"
        sql = path.read_text().split("-- Existing")[1]
        await pool.execute("--" + sql)
        assert await pool.fetchval("SELECT bool_and(sample) FROM traces")
    finally:
        await pool.close()
