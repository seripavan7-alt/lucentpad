"""Traces list filters, time window, order and facets over the deterministic sample (R2, D10)."""

from __future__ import annotations

from collections import Counter
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio

from lucentpad_server import db, sample
from lucentpad_server.app import create_app
from lucentpad_server.query import FACETS
from lucentpad_server.schema import FACET_MAX_VALUES, TraceFacets, TraceList, TraceSummary
from lucentpad_server.store import SpanStore

from .support import NOW, app_client

SPANS = sample.generate(NOW)


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


@pytest_asyncio.fixture(scope="module")
async def everything(client: httpx.AsyncClient) -> list[TraceSummary]:
    return await _all_pages(client, limit=200)


async def _all_pages(http: httpx.AsyncClient, /, **params: Any) -> list[TraceSummary]:
    out: list[TraceSummary] = []
    cursor: str | None = None
    for _ in range(1000):
        r = await http.get(
            "/v1/traces", params={**params, **({"cursor": cursor} if cursor else {})}
        )
        assert r.status_code == 200, r.text
        page = TraceList.model_validate(r.json())
        out += page.traces
        if page.next_cursor is None:
            return out
        cursor = page.next_cursor
    raise AssertionError("pagination did not terminate")


def _facet_values(t: TraceSummary, facet: str) -> list[str]:
    value = {
        "name": t.name,
        "status": t.status,
        "source": t.source,
        "client": t.client,
        "service": t.service_name,
    }.get(facet)
    if facet == "model":
        return list(t.models)
    return [value] if value is not None else []


def _matches(t: TraceSummary, params: dict[str, Any], *, exclude: str | None = None) -> bool:
    start, end = params.get("from"), params.get("to")
    if start is not None and t.start_time < start:
        return False
    if end is not None and t.start_time >= end:
        return False
    for facet in FACETS:
        wanted = params.get(facet)
        if facet == exclude or not wanted:
            continue
        wanted = [wanted] if isinstance(wanted, str) else wanted
        if not set(_facet_values(t, facet)) & set(wanted):
            return False
    return True


def _iso(params: dict[str, Any]) -> dict[str, Any]:
    return {k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in params.items()}


async def test_sample_covers_every_facet(everything: list[TraceSummary]) -> None:
    for facet in FACETS:
        assert len({v for t in everything for v in _facet_values(t, facet)}) >= 2, facet


async def test_window_boundaries(client: httpx.AsyncClient, everything: list[TraceSummary]) -> None:
    pivot = everything[len(everything) // 2]
    s = pivot.start_time
    ids: Callable[[list[TraceSummary]], set[str]] = lambda ts: {t.trace_id for t in ts}  # noqa: E731
    at_from = await _all_pages(client, limit=200, **{"from": s.isoformat()})
    assert pivot.trace_id in ids(at_from)  # from is inclusive
    assert ids(at_from) == {t.trace_id for t in everything if t.start_time >= s}
    at_to = await _all_pages(client, limit=200, to=s.isoformat())
    assert pivot.trace_id not in ids(at_to)  # to is exclusive
    assert ids(at_to) == {t.trace_id for t in everything if t.start_time < s}
    just_after = (s + timedelta(microseconds=1)).isoformat()
    exact = await _all_pages(client, limit=200, **{"from": s.isoformat(), "to": just_after})
    assert pivot.trace_id in ids(exact)
    assert all(t.start_time == s for t in exact)
    empty = await _all_pages(client, limit=200, **{"from": s.isoformat(), "to": s.isoformat()})
    assert empty == []


@pytest.mark.parametrize(
    "params",
    [
        {"name": "support-agent.run"},
        {"name": "claude-code session"},
        {"status": "error"},
        {"source": "gateway"},
        {"client": "copilot-cli"},
        {"model": "gpt-5-mini"},
        {"model": "claude-haiku-4-5"},
        {"service": "lucentpad-gateway"},
        {"from": NOW - timedelta(days=2)},
        {"to": NOW - timedelta(days=5)},
    ],
)
async def test_each_filter_alone(
    client: httpx.AsyncClient, everything: list[TraceSummary], params: dict[str, Any]
) -> None:
    got = await _all_pages(client, limit=40, **_iso(params))
    expected = [t for t in everything if _matches(t, params)]
    assert got and [t.trace_id for t in got] == [t.trace_id for t in expected]
    assert len(expected) < len(everything)


@pytest.mark.parametrize(
    "params",
    [
        {"status": ["error", "blocked"]},
        {"status": ["error", "blocked"], "source": "sdk"},
        {"client": ["claude-code", "copilot-chat"], "model": ["gpt-5", "claude-opus-5-5"]},
        {"model": ["gpt-5-mini", "claude-haiku-4-5"], "from": NOW - timedelta(days=4)},
        {"name": ["support-agent.run", "copilot-cli session"], "status": "ok", "to": NOW},
        {"service": "support-agent", "client": "copilot-cli"},  # AND across: nothing
    ],
)
async def test_or_within_and_across(
    client: httpx.AsyncClient, everything: list[TraceSummary], params: dict[str, Any]
) -> None:
    got = await _all_pages(client, limit=30, **_iso(params))
    expected = [t for t in everything if _matches(t, params)]
    assert [t.trace_id for t in got] == [t.trace_id for t in expected]


@pytest.mark.parametrize("order", ["desc", "asc"])
@pytest.mark.parametrize(
    "params",
    [{}, {"source": "gateway"}, {"status": ["error", "blocked"], "from": NOW - timedelta(days=6)}],
)
async def test_paging_returns_every_match_once(
    client: httpx.AsyncClient, everything: list[TraceSummary], order: str, params: dict[str, Any]
) -> None:
    got = await _all_pages(client, limit=7, order=order, **_iso(params))
    expected = [t for t in everything if _matches(t, params)]
    assert len({t.trace_id for t in got}) == len(got) == len(expected)
    keys = [(t.start_time, t.trace_id) for t in got]
    assert keys == sorted(keys, reverse=order == "desc")
    assert {t.trace_id for t in got} == {t.trace_id for t in expected}


async def test_order_asc_is_reverse_of_desc(
    client: httpx.AsyncClient, everything: list[TraceSummary]
) -> None:
    asc = await _all_pages(client, limit=200, order="asc")
    assert [t.trace_id for t in asc] == [t.trace_id for t in reversed(everything)]


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ({"status": "error"}, {"status": "ok"}),
        ({"status": "error"}, {"status": ["error", "ok"]}),
        ({"order": "desc"}, {"order": "asc"}),
        ({"from": (NOW - timedelta(days=3)).isoformat()}, {}),
        ({"model": "gpt-5"}, {"model": "gpt-5", "name": "x"}),
    ],
)
async def test_cursor_bound_to_order_and_filters(
    client: httpx.AsyncClient, first: dict[str, Any], second: dict[str, Any]
) -> None:
    r = await client.get("/v1/traces", params={"limit": 2, **first})
    cursor = r.json()["next_cursor"]
    assert cursor is not None
    ok = await client.get("/v1/traces", params={"limit": 2, **first, "cursor": cursor})
    assert ok.status_code == 200
    bad = await client.get("/v1/traces", params={"limit": 2, **second, "cursor": cursor})
    assert bad.status_code == 422
    assert bad.json()["detail"][0]["loc"] == ["query", "cursor"]


async def test_cursor_ignores_filter_value_order(client: httpx.AsyncClient) -> None:
    r = await client.get("/v1/traces", params={"limit": 2, "status": ["error", "ok"]})
    cursor = r.json()["next_cursor"]
    again = await client.get(
        "/v1/traces", params={"limit": 2, "status": ["ok", "error"], "cursor": cursor}
    )
    assert again.status_code == 200


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/v1/traces", {"from": "2026-09-25T12:00:00"}),
        ("/v1/traces", {"to": "2026-09-25T12:00:00"}),
        ("/v1/traces", {"since": "2026-09-25T12:00:00"}),
        ("/v1/traces", {"since": "2026-09-25T12:00:00Z", "cursor": "x"}),
        ("/v1/traces", {"order": "sideways"}),
        ("/v1/traces/facets", {"from": "2026-09-25T12:00:00"}),
        ("/v1/traces/facets", {"status": "warning"}),
    ],
)
async def test_bad_params(client: httpx.AsyncClient, path: str, params: dict[str, Any]) -> None:
    r = await client.get(path, params=params)
    assert r.status_code == 422, r.text


def _expected_facets(everything: list[TraceSummary], params: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for facet in FACETS:
        counts = Counter(
            v
            for t in everything
            if _matches(t, params, exclude=facet)
            for v in set(_facet_values(t, facet))
        )
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:FACET_MAX_VALUES]
        out[facet] = [{"value": v, "count": n} for v, n in ranked]
    return out


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"status": "error"},
        {"status": ["error", "blocked"], "source": "sdk"},
        {"model": "gpt-5", "client": ["copilot-chat", "copilot-cli"]},
        {
            "from": NOW - timedelta(days=3),
            "to": NOW - timedelta(days=1),
            "service": "support-agent",
        },
    ],
)
async def test_facets_match_model(
    client: httpx.AsyncClient, everything: list[TraceSummary], params: dict[str, Any]
) -> None:
    r = await client.get("/v1/traces/facets", params=_iso(params))
    assert r.status_code == 200, r.text
    assert TraceFacets.model_validate(r.json()).model_dump() == _expected_facets(everything, params)


async def test_facet_excludes_own_filter(client: httpx.AsyncClient) -> None:
    unfiltered = TraceFacets.model_validate((await client.get("/v1/traces/facets")).json())
    r = await client.get("/v1/traces/facets", params={"status": "error"})
    facets = TraceFacets.model_validate(r.json())
    # Ticking "error" keeps its siblings with their full counts ...
    assert facets.status == unfiltered.status
    assert {v.value for v in facets.status} == {"ok", "error", "blocked"}
    # ... while every other facet only counts error traces.
    assert sum(v.count for v in facets.source) == next(
        v.count for v in unfiltered.status if v.value == "error"
    )


@pytest.mark.parametrize(
    "params",
    [{}, {"source": "gateway"}, {"status": ["error", "ok"], "from": NOW - timedelta(days=5)}],
)
async def test_facet_counts_equal_list_totals(
    client: httpx.AsyncClient, params: dict[str, Any]
) -> None:
    r = await client.get("/v1/traces/facets", params=_iso(params))
    facets = TraceFacets.model_validate(r.json())
    for facet in FACETS:
        for fv in getattr(facets, facet):
            query = {**_iso(params), facet: fv.value}
            assert len(await _all_pages(client, limit=200, **query)) == fv.count, (facet, fv)


# --------------------------------------------------------------------------- sorts

_SORT_KEYS: dict[str, Callable[[TraceSummary], Any]] = {
    "started": lambda t: t.start_time,
    "duration": lambda t: t.duration_ms,
    "name": lambda t: t.name.encode(),  # byte order == code point order (COLLATE "C")
    "source": lambda t: t.source,
    "cost": lambda t: t.cost_usd,
}


@pytest.mark.parametrize("order", ["desc", "asc"])
@pytest.mark.parametrize("sort", list(_SORT_KEYS))
@pytest.mark.parametrize(
    ("params", "limit"),
    [
        ({}, 200),
        ({}, 9),
        ({"status": ["error", "blocked"], "source": "sdk"}, 4),
        ({"from": NOW - timedelta(days=4), "to": NOW - timedelta(days=1), "model": "gpt-5"}, 5),
    ],
)
async def test_sort_orders_and_pages(
    client: httpx.AsyncClient,
    everything: list[TraceSummary],
    sort: str,
    order: str,
    params: dict[str, Any],
    limit: int,
) -> None:
    got = await _all_pages(client, limit=limit, sort=sort, order=order, **_iso(params))
    key = _SORT_KEYS[sort]
    expected = sorted(
        (t for t in everything if _matches(t, params)),
        key=lambda t: (key(t), t.trace_id),
        reverse=order == "desc",
    )
    assert expected
    assert [t.trace_id for t in got] == [t.trace_id for t in expected]


async def test_sort_default_is_started(client: httpx.AsyncClient) -> None:
    a = (await client.get("/v1/traces", params={"limit": 20})).json()["traces"]
    b = (await client.get("/v1/traces", params={"limit": 20, "sort": "started"})).json()["traces"]
    assert a == b


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ({"sort": "name"}, {"sort": "source"}),
        ({"sort": "cost"}, {}),
        ({}, {"sort": "duration"}),
        ({"sort": "duration", "order": "asc"}, {"sort": "duration", "order": "desc"}),
        ({"sort": "name", "status": "ok"}, {"sort": "name", "status": "error"}),
    ],
)
async def test_cursor_bound_to_sort(
    client: httpx.AsyncClient, first: dict[str, Any], second: dict[str, Any]
) -> None:
    cursor = (await client.get("/v1/traces", params={"limit": 3, **first})).json()["next_cursor"]
    assert cursor is not None
    ok = await client.get("/v1/traces", params={"limit": 3, **first, "cursor": cursor})
    assert ok.status_code == 200
    bad = await client.get("/v1/traces", params={"limit": 3, **second, "cursor": cursor})
    assert bad.status_code == 422
    assert bad.json()["detail"][0]["loc"] == ["query", "cursor"]


async def test_sort_with_since(client: httpx.AsyncClient, everything: list[TraceSummary]) -> None:
    # Every sample trace was stored during this test module, so a since an hour back sees all.
    since = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    r = await client.get(
        "/v1/traces", params={"since": since, "sort": "cost", "order": "asc", "limit": 200}
    )
    assert r.status_code == 200, r.text
    page = TraceList.model_validate(r.json())
    assert page.next_cursor is None
    expected = sorted(everything, key=lambda t: (t.cost_usd, t.trace_id))[:200]
    assert [t.trace_id for t in page.traces] == [t.trace_id for t in expected]


async def test_bad_sort(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/traces", params={"sort": "tokens"})).status_code == 422
