from __future__ import annotations

import json
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import pytest

from prism_server import sample
from prism_server.pricing import PRICES, cost_usd
from prism_server.schema import Attr, EventName, Span

from .support import NOW


@pytest.fixture(scope="module")
def spans() -> list[Span]:
    return sample.generate(NOW)


def _traces(spans: list[Span]) -> dict[str, list[Span]]:
    out: dict[str, list[Span]] = defaultdict(list)
    for s in spans:
        out[s.trace_id].append(s)
    return out


def _events(spans: list[Span], name: str) -> list[tuple[Span, dict[str, object]]]:
    return [(s, dict(e.attributes)) for s in spans for e in s.events if e.name == name]


def test_deterministic(spans: list[Span]) -> None:
    assert sample.generate(NOW) == spans
    assert sample.generate(NOW, seed=7) != spans


def test_naive_now_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        sample.generate(NOW.replace(tzinfo=None))


def test_all_spans_valid(spans: list[Span]) -> None:
    for s in spans:
        assert Span.model_validate(s.model_dump(mode="json")) == s


def test_volume_and_window(spans: list[Span]) -> None:
    traces = _traces(spans)
    assert 150 <= len(traces) <= 300
    assert all(NOW - timedelta(days=7, hours=2) <= s.start_time for s in spans)
    assert all(s.end_time <= NOW for s in spans)
    assert len({(s.trace_id, s.span_id) for s in spans}) == len(spans)


def test_parent_links_resolve(spans: list[Span]) -> None:
    for trace_spans in _traces(spans).values():
        ids = {s.span_id for s in trace_spans}
        roots = [s for s in trace_spans if s.parent_span_id is None]
        assert len(roots) == 1
        assert roots[0].kind == "agent"
        for s in trace_spans:
            assert s.parent_span_id is None or s.parent_span_id in ids
            assert roots[0].start_time <= s.start_time
            assert s.end_time <= roots[0].end_time


def test_support_agent_flow(spans: list[Span]) -> None:
    sdk = [t for t in _traces(spans).values() if t[0].source == "sdk"]
    assert sdk
    for t in sdk:
        root = next(s for s in t if s.parent_span_id is None)
        assert root.attributes[Attr.SERVICE_NAME] == "support-agent"
    shapes = {
        tuple(s.name for s in sorted(t, key=lambda s: s.start_time) if s.kind != "llm") for t in sdk
    }
    assert ("support-agent.run", "lookup_order", "issue_refund", "draft_email") in shapes
    assert ("support-agent.run", "lookup_order") in shapes


def test_features_present(spans: list[Span]) -> None:
    guardrails = [s for s in spans if s.kind == "guardrail"]
    assert guardrails
    assert all(s.status == "blocked" and Attr.GUARDRAIL_RULE in s.attributes for s in guardrails)

    failovers = _events(spans, EventName.FAILOVER)
    assert failovers
    for s, attrs in failovers:
        assert s.kind == "llm"
        assert attrs[sample.FAILOVER_STATUS_CODE] == 429
        assert s.attributes[Attr.GEN_AI_RESPONSE_MODEL] == attrs[sample.FAILOVER_TO_MODEL]
        assert s.attributes[Attr.GEN_AI_REQUEST_MODEL] == attrs[sample.FAILOVER_FROM_MODEL]

    budgets = _events(spans, EventName.BUDGET_ALERT)
    assert budgets
    for _, attrs in budgets:
        spent, limit = attrs[sample.BUDGET_SPENT_USD], attrs[sample.BUDGET_LIMIT_USD]
        assert isinstance(spent, float) and isinstance(limit, float) and spent > limit

    redactions = _events(spans, EventName.REDACTION)
    assert any(a[sample.REDACTION_KIND] == "email" for _, a in redactions)

    assert any(s.status == "error" for s in spans)


def test_gateway_clients(spans: list[Span]) -> None:
    gateway = [s for s in spans if s.source == "gateway"]
    roots = [s for s in gateway if s.parent_span_id is None]
    assert {r.attributes[Attr.CLIENT] for r in roots} == {
        "claude-code",
        "copilot-chat",
        "copilot-cli",
    }
    for s in gateway:
        if s.kind == "llm":
            assert s.attributes[Attr.STREAMING] is True
    per_trace = _traces(gateway)
    assert max(sum(s.kind == "llm" for s in t) for t in per_trace.values()) > 3


def test_costs_match_price_table(spans: list[Span]) -> None:
    for s in spans:
        if s.kind != "llm":
            continue
        model = s.attributes[Attr.GEN_AI_RESPONSE_MODEL]
        assert isinstance(model, str) and model in PRICES
        in_tok = s.attributes[Attr.GEN_AI_INPUT_TOKENS]
        out_tok = s.attributes[Attr.GEN_AI_OUTPUT_TOKENS]
        assert isinstance(in_tok, int) and isinstance(out_tok, int)
        assert s.attributes[Attr.COST_USD] == cost_usd(model, in_tok, out_tok)


def test_pricing() -> None:
    assert cost_usd("claude-sonnet-5", 1_000_000, 0) == PRICES["claude-sonnet-5"].input_per_mtok
    assert cost_usd("claude-sonnet-5", 0, 0) == 0
    assert cost_usd("unknown-model", 10, 10) is None


def test_cli(tmp_path: Path) -> None:
    out = tmp_path / "spans.json"
    sample.main(["--out", str(out), "--now", NOW.isoformat()])
    data = json.loads(out.read_text())
    assert [Span.model_validate(s) for s in data["spans"]] == sample.generate(NOW)
