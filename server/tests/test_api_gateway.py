"""Gateway page queries (turns and summary endpoints) over the deterministic sample."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio

from lucentpad_server import db, sample
from lucentpad_server.app import create_app
from lucentpad_server.schema import Attr, GatewaySummary, GatewayTurn, GatewayTurnList, Span
from lucentpad_server.store import SpanStore

from .support import NOW, app_client

SPANS = sample.generate(NOW)
TURN_SPANS = {s.span_id: s for s in SPANS if s.source == "gateway" and s.kind == "llm"}
CLIENTS = ("claude-code", "copilot-chat", "copilot-cli")


def _key(t: GatewayTurn | Span) -> tuple[Any, str, str]:
    return (t.start_time, t.span_id, t.trace_id)


EXPECTED = sorted(TURN_SPANS.values(), key=_key, reverse=True)


@pytest_asyncio.fixture(scope="module")
async def client(module_db_url: str) -> AsyncIterator[httpx.AsyncClient]:
    pool = await db.create_pool(module_db_url)
    try:
        await db.migrate(pool)
        await SpanStore(pool).insert_spans(SPANS)
    finally:
        await pool.close()
    async with app_client(create_app(module_db_url, seed_sample=False)) as c:
        yield c


async def _all_turns(http: httpx.AsyncClient, /, **params: Any) -> list[GatewayTurn]:
    out: list[GatewayTurn] = []
    cursor: str | None = None
    for _ in range(1000):
        r = await http.get(
            "/v1/gateway/turns", params={**params, **({"cursor": cursor} if cursor else {})}
        )
        assert r.status_code == 200, r.text
        page = GatewayTurnList.model_validate(r.json())
        assert len(page.turns) <= params.get("limit", 50)
        out += page.turns
        if page.next_cursor is None:
            return out
        assert page.turns
        cursor = page.next_cursor
    raise AssertionError("pagination did not terminate")


def _iso(params: dict[str, Any]) -> dict[str, Any]:
    return {k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in params.items()}


def _expected(params: dict[str, Any]) -> list[Span]:
    wanted = params.get("client")
    wanted = [wanted] if isinstance(wanted, str) else wanted
    return [
        s
        for s in EXPECTED
        if ("from" not in params or s.start_time >= params["from"])
        and ("to" not in params or s.start_time < params["to"])
        and (not wanted or s.attributes.get(Attr.CLIENT) in wanted)
    ]


def test_sample_has_every_client() -> None:
    assert {s.attributes[Attr.CLIENT] for s in TURN_SPANS.values()} == set(CLIENTS)


async def test_turns_newest_first_with_fields(client: httpx.AsyncClient) -> None:
    got = await _all_turns(client, limit=200)
    assert [t.span_id for t in got] == [s.span_id for s in EXPECTED]
    for turn in got:
        span = TURN_SPANS[turn.span_id]
        attrs = span.attributes
        assert turn.trace_id == span.trace_id
        assert turn.client == attrs[Attr.CLIENT]
        assert turn.provider == attrs[Attr.GATEWAY_UPSTREAM] == attrs[Attr.GEN_AI_SYSTEM]
        assert turn.model == attrs[Attr.GEN_AI_RESPONSE_MODEL]
        assert turn.start_time == span.start_time
        expected_ms = (span.end_time - span.start_time).total_seconds() * 1000
        assert turn.duration_ms == pytest.approx(expected_ms)
        assert turn.ttfb_ms == attrs[Attr.TTFB_MS]
        assert turn.status == span.status
        assert turn.streaming is True
        assert turn.input_tokens == attrs[Attr.GEN_AI_INPUT_TOKENS]
        assert turn.output_tokens == attrs[Attr.GEN_AI_OUTPUT_TOKENS]
        assert turn.cost_usd == pytest.approx(attrs[Attr.COST_USD])
        assert turn.failover is False
        assert turn.input_preview == attrs[Attr.INPUT_PREVIEW]
        assert turn.output_preview == attrs[Attr.OUTPUT_PREVIEW]


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"client": "copilot-cli"},
        {"client": ["claude-code", "copilot-chat"]},
        {"from": NOW - timedelta(days=3), "to": NOW - timedelta(days=1)},
        {"from": NOW - timedelta(days=5), "client": ["copilot-cli", "nope"]},
        {"client": "nope"},
    ],
)
async def test_paging_returns_every_match_once(
    client: httpx.AsyncClient, params: dict[str, Any]
) -> None:
    got = await _all_turns(client, limit=7, **_iso(params))
    expected = _expected(params)
    assert [(t.trace_id, t.span_id) for t in got] == [(s.trace_id, s.span_id) for s in expected]
    assert len({t.span_id for t in got}) == len(got)
    if params.get("client") != "nope":
        assert got


async def test_window_boundaries(client: httpx.AsyncClient) -> None:
    pivot = EXPECTED[len(EXPECTED) // 2]
    s = pivot.start_time
    at_from = await _all_turns(client, limit=200, **{"from": s.isoformat()})
    assert pivot.span_id in {t.span_id for t in at_from}  # from is inclusive
    assert all(t.start_time >= s for t in at_from)
    assert len(at_from) == len([x for x in EXPECTED if x.start_time >= s])
    at_to = await _all_turns(client, limit=200, to=s.isoformat())
    assert pivot.span_id not in {t.span_id for t in at_to}  # to is exclusive
    assert len(at_to) + len(at_from) == len(EXPECTED)
    empty = await _all_turns(client, **{"from": s.isoformat(), "to": s.isoformat()})
    assert empty == []


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ({"client": "claude-code"}, {"client": "copilot-cli"}),
        ({"client": "claude-code"}, {}),
        ({"from": (NOW - timedelta(days=3)).isoformat()}, {}),
        ({"to": NOW.isoformat()}, {"to": (NOW - timedelta(seconds=1)).isoformat()}),
    ],
)
async def test_cursor_bound_to_filters(
    client: httpx.AsyncClient, first: dict[str, Any], second: dict[str, Any]
) -> None:
    r = await client.get("/v1/gateway/turns", params={"limit": 2, **first})
    cursor = r.json()["next_cursor"]
    assert cursor is not None
    ok = await client.get("/v1/gateway/turns", params={"limit": 2, **first, "cursor": cursor})
    assert ok.status_code == 200
    bad = await client.get("/v1/gateway/turns", params={"limit": 2, **second, "cursor": cursor})
    assert bad.status_code == 422
    assert bad.json()["detail"][0]["loc"] == ["query", "cursor"]


async def test_cursor_ignores_client_order(client: httpx.AsyncClient) -> None:
    params: dict[str, Any] = {"limit": 2, "client": ["copilot-cli", "claude-code"]}
    cursor = (await client.get("/v1/gateway/turns", params=params)).json()["next_cursor"]
    again = await client.get(
        "/v1/gateway/turns",
        params={"limit": 2, "client": ["claude-code", "copilot-cli"], "cursor": cursor},
    )
    assert again.status_code == 200


async def test_traces_cursor_is_rejected(client: httpx.AsyncClient) -> None:
    cursor = (await client.get("/v1/traces", params={"limit": 2})).json()["next_cursor"]
    for bad in (cursor, "garbage", "eyJnIjoxfQ"):
        r = await client.get("/v1/gateway/turns", params={"cursor": bad})
        assert r.status_code == 422, bad


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/v1/gateway/turns", {"from": "2026-09-25T12:00:00"}),
        ("/v1/gateway/turns", {"since": "2026-09-25T12:00:00"}),
        ("/v1/gateway/turns", {"since": "2026-09-25T12:00:00Z", "cursor": "x"}),
        ("/v1/gateway/turns", {"limit": 0}),
        ("/v1/gateway/turns", {"limit": 201}),
        ("/v1/gateway/summary", {"to": "2026-09-25T12:00:00"}),
    ],
)
async def test_bad_params(client: httpx.AsyncClient, path: str, params: dict[str, Any]) -> None:
    assert (await client.get(path, params=params)).status_code == 422


async def test_since_returns_recent_stores_capped(client: httpx.AsyncClient) -> None:
    # The whole sample was stored moments ago, so a `since` of now catches it (overlap),
    # capped at `limit`, newest first, no cursor; a `since` in the future catches nothing.
    body = (await client.get("/v1/gateway/turns", params={"limit": 5})).json()
    page = GatewayTurnList.model_validate(
        (await client.get("/v1/gateway/turns", params={"limit": 5, "since": body["as_of"]})).json()
    )
    assert [t.span_id for t in page.turns] == [s.span_id for s in EXPECTED[:5]]
    assert page.next_cursor is None
    later = (page.as_of + timedelta(minutes=1)).isoformat()
    r = await client.get("/v1/gateway/turns", params={"since": later})
    assert r.json()["turns"] == []


@pytest.mark.parametrize(
    "window",
    [
        {},
        {"from": NOW - timedelta(days=2)},
        {"from": NOW - timedelta(days=6), "to": NOW - timedelta(days=4)},
    ],
)
async def test_summary_equals_sums_over_turns(
    client: httpx.AsyncClient, window: dict[str, Any]
) -> None:
    r = await client.get("/v1/gateway/summary", params=_iso(window))
    assert r.status_code == 200, r.text
    summary = GatewaySummary.model_validate(r.json())
    turns = await _all_turns(client, limit=200, **_iso(window))
    by_client: dict[str, list[GatewayTurn]] = defaultdict(list)
    for t in turns:
        by_client[t.client or "other"].append(t)
    assert {c.client for c in summary.clients} == set(by_client)
    for totals in summary.clients:
        mine = by_client[totals.client]
        assert totals.turns == len(mine)
        assert totals.sessions == len({t.trace_id for t in mine})
        assert totals.input_tokens == sum(t.input_tokens or 0 for t in mine)
        assert totals.output_tokens == sum(t.output_tokens or 0 for t in mine)
        assert totals.cost_usd == pytest.approx(sum(t.cost_usd or 0 for t in mine))
    costs = [c.cost_usd for c in summary.clients]
    assert costs == sorted(costs, reverse=True)


async def test_summary_empty_window(client: httpx.AsyncClient) -> None:
    r = await client.get("/v1/gateway/summary", params={"from": NOW.isoformat()})
    assert r.status_code == 200
    assert r.json()["clients"] == []
