"""Routes that need no database. No lifespan runs here, so nothing connects."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio

from lucentpad_server.app import create_app


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
    # Until the lifespan starts an ingest pipeline (M1 step 3).
    assert r.json() == {"detail": "ingest pipeline not running"}


async def test_ingest_validates_batch(client: httpx.AsyncClient) -> None:
    r = await client.post("/v1/spans", json={"spans": []})
    assert r.status_code == 422


def test_openapi_without_database() -> None:
    schema = create_app().openapi()
    assert set(schema["paths"]) == {
        "/healthz",
        "/v1/spans",
        "/v1/ingest/stats",
        "/v1/traces",
        "/v1/traces/facets",
        "/v1/traces/{trace_id}",
    }


async def test_oversized_body_413(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUCENTPAD_INGEST_MAX_BODY_BYTES", "2000")
    app = create_app(database_url="postgresql://unused/none")
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
    big = json.dumps({"spans": [{**span, "attributes": {"k": "v" * 3000}}]}).encode()

    async def chunks() -> AsyncIterator[bytes]:
        for i in range(0, len(big), 500):
            yield big[i : i + 500]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/v1/spans", content=big, headers={"content-type": "application/json"})
        assert r.status_code == 413
        assert r.json() == {"detail": "request body larger than 2000 bytes"}
        chunked = await c.post(
            "/v1/spans", content=chunks(), headers={"content-type": "application/json"}
        )
        assert chunked.status_code == 413
        # Under the limit the body reaches the route (501: no pipeline without a lifespan).
        small = await c.post("/v1/spans", json={"spans": [span]})
        assert small.status_code == 501
        # Other paths are not limited.
        assert (await c.get("/healthz", params={"x": "y" * 3000})).status_code == 200
