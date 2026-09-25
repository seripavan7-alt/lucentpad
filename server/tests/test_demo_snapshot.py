"""The static demo's data, built from the real API over the deterministic sample.

``dashboard/src/demo/snapshot.json`` holds every trace summary and span; ``parity.json`` records
how the real API pages a set of list queries, so the dashboard's in-browser adapter can be checked
against it (``src/demo/adapter.test.ts``). Run ``make demo-snapshot`` to rewrite both files;
otherwise this test fails when the committed files have drifted from the API.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

from lucentpad_server import db, sample
from lucentpad_server.app import create_app
from lucentpad_server.store import SpanStore

from .support import NOW, app_client

DEMO_DIR = Path(__file__).resolve().parents[2] / "dashboard" / "src" / "demo"
UPDATE = os.environ.get("LUCENTPAD_UPDATE_DEMO") == "1"

# List queries whose paging the adapter must reproduce exactly.
PARITY_QUERIES: list[dict[str, Any]] = [
    {},
    {"limit": 7},
    {"status": "ok"},
    {"status": "error"},
    {"status": "blocked"},
    {"source": "sdk"},
    {"source": "gateway", "limit": 20},
    {"source": "sdk", "status": "error"},
    {"source": "gateway", "status": "blocked"},
]


async def _pages(client: httpx.AsyncClient, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Every page of a list query, as trace ids plus whether another page follows."""
    pages: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        r = await client.get(
            "/v1/traces", params={**params, **({"cursor": cursor} if cursor else {})}
        )
        r.raise_for_status()
        body = r.json()
        pages.append(
            {
                "trace_ids": [t["trace_id"] for t in body["traces"]],
                "has_next": body["next_cursor"] is not None,
            }
        )
        cursor = body["next_cursor"]
        if cursor is None:
            return pages


async def _build(client: httpx.AsyncClient) -> tuple[dict[str, Any], dict[str, Any]]:
    everything = await _pages(client, {"limit": 200})
    trace_ids = [tid for page in everything for tid in page["trace_ids"]]
    traces: list[dict[str, Any]] = []
    spans: dict[str, list[dict[str, Any]]] = {}
    for tid in trace_ids:
        r = await client.get(f"/v1/traces/{tid}")
        r.raise_for_status()
        detail = r.json()
        traces.append(detail["trace"])
        spans[tid] = detail["spans"]
    snapshot = {"generated_at": NOW.isoformat(), "traces": traces, "spans": spans}
    parity = {"queries": [{"params": q, "pages": await _pages(client, q)} for q in PARITY_QUERIES]}
    return snapshot, parity


def _write(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, separators=(",", ":"), sort_keys=True) + "\n")


async def test_demo_snapshot_matches_api(db_url: str) -> None:
    pool = await db.create_pool(db_url)
    try:
        await db.migrate(pool)
        await SpanStore(pool).insert_spans(sample.generate(NOW))
    finally:
        await pool.close()
    async with app_client(create_app(db_url, seed_sample=False)) as client:
        snapshot, parity = await _build(client)

    files = {DEMO_DIR / "snapshot.json": snapshot, DEMO_DIR / "parity.json": parity}
    if UPDATE:
        DEMO_DIR.mkdir(parents=True, exist_ok=True)
        for path, data in files.items():
            _write(path, data)
        return
    for path, data in files.items():
        assert path.exists(), f"{path.name} missing: run 'make demo-snapshot'"
        assert json.loads(path.read_text()) == data, (
            f"{path.name} is stale: run 'make demo-snapshot'"
        )
