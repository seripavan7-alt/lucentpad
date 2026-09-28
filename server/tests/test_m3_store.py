"""M3 queries on real Postgres: guardrail events + summary, cost series, eval runs, and the
migration backfill. The deterministic sample (at ``NOW``) plus a little real data."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg
import httpx
import pytest
import pytest_asyncio

from lucentpad_server import db, sample, store
from lucentpad_server.app import create_app
from lucentpad_server.schema import (
    Attr,
    EvalCaseResult,
    EvalCheckResult,
    EvalRunIn,
    EventName,
    Span,
    SpanEvent,
)
from lucentpad_server.store import SpanStore, bucket_seconds_for

from .support import NOW, app_client

REAL_TRACE = "feed" + "0" * 27 + "1"
SPANS = sample.generate(NOW)


def _real_spans() -> list[Span]:
    t = NOW - timedelta(hours=1)
    common = {Attr.SERVICE_NAME: "my-agent", Attr.CLIENT: "my-client"}
    return [
        Span(
            trace_id=REAL_TRACE,
            span_id="a" * 16,
            name="my-agent.run",
            kind="agent",
            source="sdk",
            start_time=t,
            end_time=t + timedelta(seconds=3),
            attributes=common,
        ),
        Span(
            trace_id=REAL_TRACE,
            span_id="b" * 16,
            parent_span_id="a" * 16,
            name="chat gpt-5",
            kind="llm",
            source="sdk",
            start_time=t + timedelta(milliseconds=5),
            end_time=t + timedelta(seconds=1),
            attributes={
                **common,
                Attr.GEN_AI_REQUEST_MODEL: "gpt-5",
                Attr.GEN_AI_INPUT_TOKENS: 1000,
                Attr.GEN_AI_OUTPUT_TOKENS: 100,
            },
            events=[
                SpanEvent(
                    name=EventName.REDACTION,
                    time=t + timedelta(milliseconds=5),
                    attributes={Attr.REDACTION_KIND: "api_key", Attr.REDACTION_COUNT: 2},
                )
            ],
        ),
        Span(
            trace_id=REAL_TRACE,
            span_id="c" * 16,
            parent_span_id="a" * 16,
            name="guardrail no_secrets",
            kind="guardrail",
            source="sdk",
            start_time=t + timedelta(seconds=2),
            end_time=t + timedelta(seconds=2),
            status="blocked",
            status_message="fallback message",
            attributes={
                **common,
                Attr.GUARDRAIL_RULE: "no_secrets",
                Attr.GUARDRAIL_REASON: 'keyword "password": no secrets',
            },
            events=[
                SpanEvent(
                    name=EventName.GUARDRAIL_BLOCK,
                    time=t + timedelta(seconds=2),
                    attributes={Attr.GUARDRAIL_RULE: "no_secrets"},
                )
            ],
        ),
    ]


def _expected_events(spans: list[Span]) -> list[tuple[str, str, str, int]]:
    """(kind, rule-or-redaction-kind, span_id, count) for every event in ``spans``."""
    out: list[tuple[str, str, str, int]] = []
    for s in spans:
        if s.kind == "guardrail":
            rule = s.attributes.get(Attr.GUARDRAIL_RULE)
            out.append(("block", str(rule), s.span_id, 1))
        for e in s.events:
            if e.name == EventName.REDACTION:
                count = e.attributes[Attr.REDACTION_COUNT]
                assert isinstance(count, int)
                out.append(("redaction", str(e.attributes[Attr.REDACTION_KIND]), s.span_id, count))
            elif e.name == EventName.BUDGET_ALERT:
                out.append(("budget", "budget", s.span_id, 1))
    return sorted(out)


def _key(e: dict[str, Any]) -> tuple[str, str, str, int]:
    label = {"block": e["rule"], "redaction": e["redaction_kind"], "budget": "budget"}[e["kind"]]
    return (e["kind"], label, e["span_id"], e["count"])


@pytest_asyncio.fixture(scope="module")
async def client(module_db_url: str) -> AsyncIterator[httpx.AsyncClient]:
    pool = await db.create_pool(module_db_url)
    try:
        await db.migrate(pool)
        s = SpanStore(pool)
        await s.insert_spans(SPANS, sample=True)
        await s.insert_spans(_real_spans())  # marks real data: no sample shift
        await s.insert_eval_runs(sample.eval_runs(NOW, SPANS), sample=True)
    finally:
        await pool.close()
    async with app_client(create_app(module_db_url, seed_sample=False)) as c:
        yield c


async def _all_events(client: httpx.AsyncClient, **params: Any) -> list[list[dict[str, Any]]]:
    pages: list[list[dict[str, Any]]] = []
    cursor = None
    while True:
        q = {**params, **({"cursor": cursor} if cursor else {})}
        r = await client.get("/v1/guardrails/events", params=q)
        assert r.status_code == 200, r.text
        body = r.json()
        pages.append(body["events"])
        cursor = body["next_cursor"]
        if cursor is None:
            return pages


# --------------------------------------------------------------------------- guardrail events


async def test_events_cover_blocks_and_redactions(client: httpx.AsyncClient) -> None:
    (page,) = await _all_events(client, limit=200)
    everything = SPANS + _real_spans()
    assert sorted(_key(e) for e in page) == _expected_events(everything)
    assert {e["kind"] for e in page} == {"block", "redaction", "budget"}
    budget = next(e for e in page if e["kind"] == "budget")
    assert budget["budget_limit_usd"] < budget["budget_spent_usd"] and budget["count"] == 1
    times = [(e["time"], e["trace_id"], e["span_id"]) for e in page]
    assert times == sorted(times, reverse=True)
    real_block = next(e for e in page if e["rule"] == "no_secrets")
    assert real_block == {
        "kind": "block",
        "time": (NOW - timedelta(hours=1) + timedelta(seconds=2))
        .isoformat()
        .replace("+00:00", "Z"),
        "trace_id": REAL_TRACE,
        "span_id": "c" * 16,
        "source": "sdk",
        "client": "my-client",
        "rule": "no_secrets",
        "reason": 'keyword "password": no secrets',  # the attribute wins over status_message
        "redaction_kind": None,
        "count": 1,
        "budget_limit_usd": None,
        "budget_spent_usd": None,
        "budget_scope": None,
    }
    sample_block = next(e for e in page if e["kind"] == "block" and e["trace_id"] != REAL_TRACE)
    assert sample_block["rule"] == "refund_limit_200"
    assert sample_block["reason"].startswith("refund of $")  # falls back to status_message
    assert sample_block["client"] == "sdk"


async def test_events_paging_is_stable(client: httpx.AsyncClient) -> None:
    (single,) = await _all_events(client, limit=200)
    pages = await _all_events(client, limit=7)
    assert all(len(p) == 7 for p in pages[:-1]) and 0 < len(pages[-1]) <= 7
    assert [e for p in pages for e in p] == single


async def test_events_filters(client: httpx.AsyncClient) -> None:
    (everything,) = await _all_events(client, limit=200)
    for kind in ("block", "redaction", "budget"):
        pages = await _all_events(client, kind=kind, limit=5)
        assert [e for p in pages for e in p] == [e for e in everything if e["kind"] == kind]
    (two,) = await _all_events(client, kind=["block", "budget"], limit=200)
    assert two == [e for e in everything if e["kind"] != "redaction"]
    (every,) = await _all_events(client, kind=["block", "redaction", "budget"], limit=200)
    assert every == everything
    lo, hi = NOW - timedelta(days=3), NOW - timedelta(days=1)
    (window,) = await _all_events(client, **{"from": lo.isoformat(), "to": hi.isoformat()})
    assert (
        window == [e for e in everything if lo <= datetime.fromisoformat(e["time"]) < hi] and window
    )
    (real,) = await _all_events(client, hide_sample="true", limit=200)
    assert sorted(_key(e) for e in real) == _expected_events(_real_spans())


async def test_events_cursor_errors(client: httpx.AsyncClient) -> None:
    r = await client.get("/v1/guardrails/events", params={"limit": 3})
    cursor = r.json()["next_cursor"]
    assert cursor
    for params in (
        {"cursor": cursor, "kind": "block"},
        {"cursor": cursor, "hide_sample": "true"},
        {"cursor": "not-a-cursor"},
        {"cursor": cursor, "since": NOW.isoformat()},
    ):
        r = await client.get("/v1/guardrails/events", params={"limit": 3, **params})
        assert r.status_code == 422, params
    r = await client.get("/v1/guardrails/events", params={"from": "2026-09-25T12:00:00"})
    assert r.status_code == 422  # naive timestamp


async def test_guardrail_summary(client: httpx.AsyncClient) -> None:
    r = await client.get("/v1/guardrails/summary")
    assert r.status_code == 200
    body = r.json()
    blocks = Counter(
        str(s.attributes[Attr.GUARDRAIL_RULE])
        for s in SPANS + _real_spans()
        if s.kind == "guardrail"
    )
    assert body["blocks"] == [
        {"rule": rule, "blocks": n}
        for rule, n in sorted(blocks.items(), key=lambda x: (-x[1], x[0]))
    ]
    values: Counter[str] = Counter()
    for kind, label, _, count in _expected_events(SPANS + _real_spans()):
        if kind == "redaction":
            values[label] += count
    assert body["redactions"] == [
        {"value": v, "count": n} for v, n in sorted(values.items(), key=lambda x: (-x[1], x[0]))
    ]
    real = (await client.get("/v1/guardrails/summary", params={"hide_sample": "true"})).json()
    assert real["blocks"] == [{"rule": "no_secrets", "blocks": 1}]
    assert real["redactions"] == [{"value": "api_key", "count": 2}]
    empty = (await client.get("/v1/guardrails/summary", params={"from": NOW.isoformat()})).json()
    assert empty["blocks"] == [] and empty["redactions"] == []


async def test_events_since_and_migration_backfill(db_url: str) -> None:
    pool = await db.create_pool(db_url)
    try:
        await db.migrate(pool)
        await SpanStore(pool).insert_spans(SPANS, sample=True)
        written = await pool.fetch("SELECT * FROM guardrail_events ORDER BY 1, 2, 3")
        # Re-run migrations 0006 + 0007 over the stored spans: their backfill must match the
        # writer.
        await pool.execute(
            "DROP TABLE guardrail_events, eval_runs; DROP INDEX spans_cost_time_idx;"
            " DROP STATISTICS spans_cost_bucket_300_stats, spans_cost_bucket_3600_stats,"
            " spans_cost_bucket_86400_stats;"
            " DELETE FROM schema_migrations WHERE version LIKE '0006%' OR version LIKE '0007%'"
        )
        await db.migrate(pool)
        backfilled = await pool.fetch("SELECT * FROM guardrail_events ORDER BY 1, 2, 3")
        assert [dict(r) for r in backfilled] == [dict(r) for r in written] and written
        await pool.execute("UPDATE guardrail_events SET stored_at = now() - interval '1 hour'")
        await pool.execute("INSERT INTO lucentpad_meta (key, value) VALUES ('real_data_at', 'x')")
    finally:
        await pool.close()
    async with app_client(create_app(db_url, seed_sample=False)) as client:
        first = (await client.get("/v1/guardrails/events")).json()
        assert first["events"] and first["next_cursor"]
        polled = await client.get("/v1/guardrails/events", params={"since": first["as_of"]})
        assert polled.json()["events"] == []
        await asyncio.sleep(0.01)
        new = _real_spans()
        r = await client.post("/v1/spans", json={"spans": [s.model_dump(mode="json") for s in new]})
        assert r.status_code == 202
        for _ in range(100):
            polled = await client.get("/v1/guardrails/events", params={"since": first["as_of"]})
            if len(polled.json()["events"]) == 2:
                break
            await asyncio.sleep(0.02)
        body = polled.json()
        assert sorted(e["span_id"] for e in body["events"]) == ["b" * 16, "c" * 16]
        assert body["next_cursor"] is None


def test_sql_literals_match_contract_constants() -> None:
    """The writer's SQL (and migration 0006) inline these attribute keys as literals."""
    sql = store._MERGE_STAGE
    for key in (
        Attr.CLIENT,
        Attr.GUARDRAIL_RULE,
        Attr.GUARDRAIL_REASON,
        Attr.REDACTION_KIND,
        Attr.REDACTION_COUNT,
        EventName.REDACTION,
    ):
        assert f"'{key}'" in sql or f'"{key}"' in sql
        migration = dict(db.migration_files())["0006_guardrails_costs_evals"]
        assert key in migration


# --------------------------------------------------------------------------- costs


async def _trace_costs(client: httpx.AsyncClient, **params: Any) -> Decimal:
    total = Decimal(0)
    cursor = None
    while True:
        q = {"limit": 200, **params, **({"cursor": cursor} if cursor else {})}
        body = (await client.get("/v1/traces", params=q)).json()
        total += sum((Decimal(str(t["cost_usd"])) for t in body["traces"]), Decimal(0))
        cursor = body["next_cursor"]
        if cursor is None:
            return total


@pytest.mark.parametrize("group_by", ["model", "client", "service"])
async def test_cost_series_sums_equal_trace_totals(
    client: httpx.AsyncClient, group_by: str
) -> None:
    r = await client.get("/v1/costs", params={"group_by": group_by})
    assert r.status_code == 200
    body = r.json()
    assert body["group_by"] == group_by
    assert body["bucket_seconds"] == 86400  # open window: oldest priced span .. now (> 2 days)
    traces = await _trace_costs(client)
    assert abs(Decimal(str(body["total_cost_usd"])) - traces) < Decimal("1e-6")
    points = body["points"]
    assert abs(sum(p["cost_usd"] for p in points) - body["total_cost_usd"]) < 1e-6
    for p in points:
        bucket = datetime.fromisoformat(p["bucket"])
        assert bucket.timestamp() % 86400 == 0  # aligned (UTC midnight)
        assert p["calls"] > 0
    keys = [(p["bucket"], p["group"]) for p in points]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)
    groups = {p["group"] for p in points}
    expected = {
        "model": {"claude-sonnet-5", "claude-haiku-4-5", "claude-opus-5-5", "gpt-5", "gpt-5-mini"},
        "client": {"sdk", "claude-code", "copilot-chat", "copilot-cli", "my-client"},
        "service": {"support-agent", "lucentpad-gateway", "my-agent"},
    }[group_by]
    assert groups == expected


async def test_cost_series_windows_and_buckets(client: httpx.AsyncClient) -> None:
    for window, seconds in (
        (timedelta(hours=1), 300),
        (timedelta(hours=2), 300),
        (timedelta(hours=30), 3600),
        (timedelta(days=2), 3600),
        (timedelta(days=4), 86400),
    ):
        lo, hi = NOW - window, NOW
        params = {"from": lo.isoformat(), "to": hi.isoformat()}
        body = (await client.get("/v1/costs", params=params)).json()
        assert body["bucket_seconds"] == seconds == bucket_seconds_for(window)
        total = await _trace_costs_in_window(client, lo, hi)
        assert abs(Decimal(str(body["total_cost_usd"])) - total) < Decimal("1e-6")
        for p in body["points"]:
            b = datetime.fromisoformat(p["bucket"])
            assert b.timestamp() % seconds == 0
            assert lo - timedelta(seconds=seconds) < b < hi
    empty = (await client.get("/v1/costs", params={"from": NOW.isoformat()})).json()
    assert empty["points"] == [] and empty["total_cost_usd"] == 0


async def _trace_costs_in_window(client: httpx.AsyncClient, lo: datetime, hi: datetime) -> Decimal:
    """Sum of span costs with start_time in [lo, hi), from trace detail (the reference)."""
    total = Decimal(0)
    cursor = None
    while True:
        q: dict[str, str | int] = {"limit": 200, "to": hi.isoformat()}
        if cursor:
            q["cursor"] = cursor
        body = (await client.get("/v1/traces", params=q)).json()
        for t in body["traces"]:
            end = datetime.fromisoformat(t["start_time"]) + timedelta(milliseconds=t["duration_ms"])
            if end < lo:
                continue
            spans = (await client.get(f"/v1/traces/{t['trace_id']}")).json()["spans"]
            for s in spans:
                start = datetime.fromisoformat(s["start_time"])
                cost = s["attributes"].get(Attr.COST_USD)
                if lo <= start < hi and cost is not None:
                    total += Decimal(str(cost))
        cursor = body["next_cursor"]
        if cursor is None:
            return total


async def test_cost_series_hide_sample(client: httpx.AsyncClient) -> None:
    body = (await client.get("/v1/costs", params={"hide_sample": "true"})).json()
    (point,) = body["points"]
    assert point["group"] == "gpt-5" and point["calls"] == 1
    assert point["input_tokens"] == 1000 and point["output_tokens"] == 100
    assert body["total_cost_usd"] == pytest.approx(1000 * 1.25e-6 + 100 * 10e-6)
    assert body["bucket_seconds"] == 86400  # oldest real priced span (1 h before NOW) .. now


# --------------------------------------------------------------------------- evals


def _run(started: datetime, suite: str = "unit", **kw: Any) -> EvalRunIn:
    cases = [
        EvalCaseResult(
            case="status",
            passed=True,
            baseline_passed=True,
            checks=[EvalCheckResult(check="tool_called: lookup_order", passed=True)],
            cost_usd=0.0021,
            latency_ms=1834.5,
            trace_id="1" * 32,
            output_preview="Order 1042 was delivered.",
        ),
        EvalCaseResult(
            case="refund",
            passed=False,
            baseline_passed=True,
            checks=[
                EvalCheckResult(check="contains: refund", passed=False, detail="not found"),
            ],
            cost_usd=None,
            latency_ms=None,
            trace_id=None,
            output_preview=None,
        ),
        EvalCaseResult(
            case="new_case",
            passed=False,
            baseline_passed=None,
            checks=[],
            cost_usd=0.001,
            latency_ms=900.0,
            trace_id=None,
            output_preview=None,
        ),
    ]
    data: dict[str, Any] = {
        "suite": suite,
        "status": "regressed",
        "started_at": started,
        "duration_ms": 5123.25,
        "model": "claude-haiku-4-5",
        "git_sha": "a" * 40,
        "git_ref": "main",
        "ci_url": "https://ci.example/run/1",
        "cost_usd": 0.0031,
        "baseline_cost_usd": 0.0029,
        "cases": cases,
        **kw,
    }
    return EvalRunIn.model_validate(data)


async def test_eval_run_round_trip(db_url: str) -> None:
    async with app_client(create_app(db_url, seed_sample=False)) as client:
        run = _run(NOW)
        r = await client.post("/v1/evals/runs", json=run.model_dump(mode="json"))
        assert r.status_code == 201
        created = r.json()
        run_id = created.pop("id")
        assert len(run_id) == 16 and all(c in "0123456789abcdef" for c in run_id)
        assert created == run.model_dump(mode="json")
        got = (await client.get(f"/v1/evals/runs/{run_id}")).json()
        assert got == {"id": run_id, **run.model_dump(mode="json")}
        listed = (await client.get("/v1/evals/runs")).json()
        assert listed["next_cursor"] is None
        assert listed["runs"] == [
            {
                "id": run_id,
                "suite": "unit",
                "status": "regressed",
                "started_at": got["started_at"],
                "passed": 1,
                "failed": 2,
                "regressions": 1,
                "cost_usd": 0.0031,
                "baseline_cost_usd": run.baseline_cost_usd,
                "git_sha": "a" * 40,
                "git_ref": "main",
                "ci_url": "https://ci.example/run/1",
            }
        ]
        assert (await client.get("/v1/evals/runs/nope")).status_code == 404
        assert (await client.get(f"/v1/evals/runs/{'0' * 16}")).status_code == 404
        bad = run.model_dump(mode="json") | {"status": "great"}
        assert (await client.post("/v1/evals/runs", json=bad)).status_code == 422


async def test_eval_run_list_paging_and_suite(db_url: str) -> None:
    async with app_client(create_app(db_url, seed_sample=False)) as client:
        ids: dict[str, list[str]] = {"a": [], "b": []}
        for i in range(7):
            suite = "a" if i % 3 else "b"
            run = _run(NOW - timedelta(hours=i), suite=suite)
            r = await client.post("/v1/evals/runs", json=run.model_dump(mode="json"))
            ids[suite].append(r.json()["id"])
        # Same start time twice: ties break on id.
        same = [
            (
                await client.post(
                    "/v1/evals/runs", json=_run(NOW - timedelta(days=1)).model_dump(mode="json")
                )
            ).json()["id"]
            for _ in range(2)
        ]
        pages: list[list[str]] = []
        cursor = None
        while True:
            q = {"limit": 3, **({"cursor": cursor} if cursor else {})}
            body = (await client.get("/v1/evals/runs", params=q)).json()
            pages.append([r["id"] for r in body["runs"]])
            cursor = body["next_cursor"]
            if cursor is None:
                break
        flat = [i for p in pages for i in p]
        assert [len(p) for p in pages] == [3, 3, 3]
        expected = [
            ids["b"][0],
            ids["a"][0],
            ids["a"][1],
            ids["b"][1],
            ids["a"][2],
            ids["a"][3],
            ids["b"][2],
            *sorted(same, reverse=True),
        ]
        assert flat == expected
        only_a = (await client.get("/v1/evals/runs", params={"suite": "a"})).json()
        assert [r["id"] for r in only_a["runs"]] == ids["a"]
        page = (await client.get("/v1/evals/runs", params={"suite": "a", "limit": 1})).json()
        other = await client.get("/v1/evals/runs", params={"cursor": page["next_cursor"]})
        assert other.status_code == 422
        bad = await client.get("/v1/evals/runs", params={"cursor": "zzz"})
        assert bad.status_code == 422


async def test_lifespan_seeds_sample_eval_runs(db_url: str) -> None:
    async with app_client(create_app(db_url, seed_sample=True)) as client:
        runs = (await client.get("/v1/evals/runs")).json()["runs"]
        traces = (await client.get("/v1/traces", params={"limit": 1})).json()["traces"]
    assert len(runs) == 8
    assert [r["status"] for r in runs].count("regressed") == 1
    regressed = next(r for r in runs if r["status"] == "regressed")
    assert regressed["regressions"] == 1 and regressed["ci_url"]
    # Lined up with the (shifted) sample: the newest run is within the last day.
    newest = datetime.fromisoformat(runs[0]["started_at"])
    assert datetime.now(UTC) - newest < timedelta(days=1)
    assert datetime.fromisoformat(traces[0]["start_time"]) > newest - timedelta(days=1)
    conn = await asyncpg.connect(db_url)
    try:
        assert await conn.fetchval("SELECT bool_and(sample) FROM eval_runs")
    finally:
        await conn.close()
    # A second start neither duplicates them nor moves them out of line.
    async with app_client(create_app(db_url, seed_sample=True)) as client:
        again = (await client.get("/v1/evals/runs")).json()["runs"]
    assert [r["id"] for r in again] == [r["id"] for r in runs]


async def test_sample_eval_runs_link_to_sample_traces(client: httpx.AsyncClient) -> None:
    runs = (await client.get("/v1/evals/runs")).json()["runs"]
    assert len(runs) == 8
    trace_ids = {s.trace_id for s in SPANS}
    for summary in runs:
        run = (await client.get(f"/v1/evals/runs/{summary['id']}")).json()
        linked = [c["trace_id"] for c in run["cases"] if c["trace_id"]]
        assert linked and set(linked) <= trace_ids
        assert summary["passed"] + summary["failed"] == len(run["cases"]) == 6


async def test_budget_alerts_and_eval_sample_filter(client: httpx.AsyncClient) -> None:
    summary = (await client.get("/v1/guardrails/summary")).json()
    alerts = sum(1 for s in SPANS for e in s.events if e.name == EventName.BUDGET_ALERT)
    assert summary["budget_alerts"] == alerts > 0
    runs = (await client.get("/v1/evals/runs", params={"limit": 200})).json()["runs"]
    assert runs and any(r["baseline_cost_usd"] is not None for r in runs)
    hidden = (await client.get("/v1/evals/runs", params={"hide_sample": True})).json()
    assert hidden["runs"] == []  # every run in this fixture is sample data
