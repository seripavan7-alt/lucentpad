"""The static demo's data, built from the real API over the deterministic sample.

``dashboard/src/demo/snapshot.json`` holds every trace summary and span; ``parity.json`` records
how the real API pages a set of list queries, so the dashboard's in-browser adapter can be checked
against it (``src/demo/adapter.test.ts``). Run ``make demo-snapshot`` to rewrite both files;
otherwise this test fails when the committed files have drifted from the API.
"""

from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx

from lucentpad_server import db, sample
from lucentpad_server.app import create_app
from lucentpad_server.store import SpanStore

from .support import NOW, app_client

DEMO_DIR = Path(__file__).resolve().parents[2] / "dashboard" / "src" / "demo"
UPDATE = os.environ.get("LUCENTPAD_UPDATE_DEMO") == "1"


def _ago(**kw: float) -> str:
    """An absolute ISO time relative to the snapshot's fixed NOW (the unshifted snapshot)."""
    return (NOW - timedelta(**kw)).isoformat()


# List queries whose paging the adapter must reproduce exactly. A list value is a repeated
# query parameter (`?status=error&status=blocked`): OR within a filter, AND across filters.
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
    # R2 / D10: multi-value filters, time window, oldest-first order.
    {"status": ["error", "blocked"], "limit": 10},
    {"name": "support-agent.run", "limit": 25},
    {"name": ["claude-code session", "copilot-cli session"]},
    {"client": ["copilot-chat", "copilot-cli"], "limit": 15},
    {"model": "claude-haiku-4-5"},
    {"model": ["gpt-5", "claude-opus-5-5"], "status": "ok"},
    {"service": "lucentpad-gateway", "source": "gateway"},
    {"from": _ago(days=2)},
    {"to": _ago(days=5), "limit": 20},
    {"from": _ago(days=4), "to": _ago(days=3), "source": "sdk"},
    {"order": "asc"},
    {"order": "asc", "limit": 9, "status": ["error", "blocked"]},
    {"order": "asc", "from": _ago(days=1), "model": "claude-sonnet-5"},
    # Sort by column (ties on trace_id in the same direction; name/source in code point order).
    {"sort": "started", "order": "asc", "limit": 50},
    {"sort": "duration"},
    {"sort": "duration", "order": "asc", "limit": 11},
    {"sort": "duration", "status": ["error", "blocked"], "limit": 6},
    {"sort": "name"},
    {"sort": "name", "order": "asc", "limit": 13},
    {"sort": "name", "order": "asc", "from": _ago(days=3), "source": "sdk", "limit": 8},
    {"sort": "source", "limit": 17},
    {"sort": "source", "order": "asc"},
    {"sort": "source", "order": "asc", "model": "gpt-5", "limit": 5},
    {"sort": "cost"},
    {"sort": "cost", "order": "asc", "limit": 12},
    {"sort": "cost", "from": _ago(days=4), "to": _ago(days=1), "client": "copilot-cli", "limit": 4},
]

# Facet queries (`GET /v1/traces/facets`) whose counts the adapter must reproduce exactly.
FACET_PARITY_QUERIES: list[dict[str, Any]] = [
    {},
    {"status": "error"},
    {"status": ["error", "blocked"], "source": "sdk"},
    {"model": "gpt-5", "client": ["copilot-chat", "copilot-cli"]},
    {"from": _ago(days=3), "to": _ago(days=1), "service": "support-agent"},
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
    facets = []
    for q in FACET_PARITY_QUERIES:
        r = await client.get("/v1/traces/facets", params=q)
        r.raise_for_status()
        facets.append({"params": q, "facets": r.json()})
    parity = {
        "queries": [{"params": q, "pages": await _pages(client, q)} for q in PARITY_QUERIES],
        "facets": facets,
    }
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
