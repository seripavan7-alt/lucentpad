"""Routes that need no database. No lifespan runs here, so nothing connects."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest_asyncio

from prism_server.app import create_app


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=create_app(database_url="postgresql://unused/none"))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_healthz(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_ingest_not_implemented(client: httpx.AsyncClient) -> None:
    t = datetime(2026, 9, 25, 12, tzinfo=UTC).isoformat()
    span = {
        "trace_id": "0af7651916cd43dd8448eb211c80319c",
        "span_id": "b7ad6b7169203331",
        "name": "x",
        "kind": "agent",
        "source": "sdk",
        "start_time": t,
        "end_time": t,
    }
    r = await client.post("/v1/spans", json={"spans": [span]})
    assert r.status_code == 501
    assert r.json() == {"detail": "ingest arrives in M1"}


async def test_ingest_validates_batch(client: httpx.AsyncClient) -> None:
    r = await client.post("/v1/spans", json={"spans": []})
    assert r.status_code == 422


def test_openapi_without_database() -> None:
    schema = create_app().openapi()
    assert set(schema["paths"]) == {"/healthz", "/v1/spans", "/v1/traces", "/v1/traces/{trace_id}"}
