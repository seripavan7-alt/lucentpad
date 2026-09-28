"""Demo steps 3-5 in mock mode, end to end: the agent against a fake LucentPad API that serves
the server's built-in rules and price table (conftest.FakeApi). No network, no keys."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import yaml
from agent_fake_api import FakeApi

import lucentpad
from lucentpad import _core
from lucentpad._exporter import Exporter
from lucentpad_server.guardrails.rules import BUILT_IN_RULES
from lucentpad_server.schema import Attr, EventName
from support_agent import __main__ as cli
from support_agent import config
from support_agent.rules import local_rules

EMAIL = "maya.patel@example.com"
ENDPOINT = "http://lucentpad.test:8000"


@pytest.fixture
def fake_api() -> FakeApi:
    return FakeApi()


@pytest.fixture
def api(fake_api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeApi]:
    def exporter(endpoint: str) -> Exporter:
        return Exporter(endpoint, transport=httpx.MockTransport(fake_api.handler), interval=0.01)

    monkeypatch.setattr(_core, "Exporter", exporter)
    yield fake_api
    lucentpad.shutdown()


def agent(*args: str) -> None:
    cli.main([*args, "--mock-llm", "--mock-delay", "0.02", "--endpoint", ENDPOINT])


def by_start(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(spans, key=lambda s: (s["start_time"], s["kind"] != "agent"))


def named(spans: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(s for s in spans if s["name"] == name)


def events(spans: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [e for s in spans for e in s["events"] if e["name"] == name]


def test_step3_customer_email_is_redacted_in_previews(
    api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    agent("Where's order 1042? I want a refund.")
    assert "refunded $77.00 for order 1042" in capsys.readouterr().out
    spans = api.spans
    assert EMAIL not in json.dumps(spans)  # nowhere, in any span

    draft = named(spans, "draft_email")
    preview = draft["attributes"][Attr.INPUT_PREVIEW]
    assert json.loads(preview)["to"] == "[REDACTED:email]"
    assert "Your refund for order 1042" in preview  # the rest of the arguments survive
    redactions = [e for e in draft["events"] if e["name"] == EventName.REDACTION]
    assert redactions and redactions[0]["attributes"][Attr.REDACTION_KIND] == "email"
    assert redactions[0]["attributes"][Attr.REDACTION_COUNT] >= 1

    lookup = named(spans, "lookup_order")
    assert json.loads(lookup["attributes"][Attr.INPUT_PREVIEW]) == {"order_id": "1042"}
    order = json.loads(lookup["attributes"][Attr.OUTPUT_PREVIEW])
    assert order["customer"] == {"name": "Maya Patel", "email": "[REDACTED:email]"}
    assert any(e["name"] == EventName.REDACTION for e in lookup["events"])
    assert not events(spans, EventName.BUDGET_ALERT)  # $0.50 is plenty


def test_step4_refund_over_the_limit_is_blocked_by_the_api_rule(
    api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    agent("Order 1057 arrived broken, I want a refund.")
    out, err = capsys.readouterr()
    assert out.strip() == config.HANDOFF_REFUND
    assert "colleague" in out and "one business day" in out
    assert "blocked by guardrail refund_limit" in err
    assert "GET /v1/guardrails/rules" in api.paths  # the rule came from the API

    spans = by_start(api.spans)
    root = spans[0]
    assert root["kind"] == "agent" and root["status"] == "ok"  # handled, not a crash
    assert root["attributes"][Attr.OUTPUT_PREVIEW] == config.HANDOFF_REFUND
    assert [(s["kind"], s["name"]) for s in spans[1:]] == [
        ("llm", "chat claude-sonnet-5"),
        ("tool", "lookup_order"),
        ("llm", "chat claude-sonnet-5"),
        ("guardrail", "guardrail refund_limit"),  # issue_refund never ran; no email drafted
    ]
    block = spans[-1]
    assert block["status"] == "blocked"
    assert block["parent_span_id"] == root["span_id"]
    assert block["attributes"][Attr.GUARDRAIL_RULE] == "refund_limit"
    assert block["attributes"][Attr.GEN_AI_TOOL_NAME] == "issue_refund"
    assert "Refunds over $200" in block["attributes"][Attr.GUARDRAIL_REASON]
    assert [e["name"] for e in block["events"]] == [EventName.GUARDRAIL_BLOCK]


def test_step5_tiny_budget_fires_one_alert(api: FakeApi) -> None:
    agent("Where are orders 1042, 1043 and 1057?", "--budget", "0.001")
    spans = api.spans
    llm = [s for s in spans if s["kind"] == "llm"]
    assert len(llm) == 4  # three lookups, one per turn, then the summary
    alerts = events(spans, EventName.BUDGET_ALERT)
    assert len(alerts) == 1
    a = alerts[0]["attributes"]
    assert a[Attr.BUDGET_LIMIT_USD] == 0.001
    assert a[Attr.BUDGET_SPENT_USD] > 0.001
    assert a[Attr.BUDGET_SCOPE] == "run"


def test_reschedule_uses_the_reschedule_tool(
    api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    agent("Where's order 1061? Can you reschedule delivery to 10 am tomorrow?")
    assert "rescheduled the delivery for tomorrow 10:00-10:30" in capsys.readouterr().out
    spans = by_start(api.spans)
    assert [s["name"] for s in spans if s["kind"] == "tool"] == [
        "lookup_order",
        "reschedule_delivery",
    ]
    moved = named(spans, "reschedule_delivery")
    assert moved["status"] == "ok"
    assert json.loads(moved["attributes"][Attr.INPUT_PREVIEW]) == {
        "order_id": "1061",
        "window": "tomorrow 10:00-10:30",
    }


def test_shipped_orders_cannot_be_rescheduled(
    api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    agent("Where's order 1043? Could it come tomorrow instead?")
    assert "can't be rescheduled" in capsys.readouterr().out
    assert [s["name"] for s in api.spans if s["kind"] == "tool"] == ["lookup_order"]


def test_local_rules_block_without_asking_the_api(
    api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api.rules = None  # a server without rules would let the refund through
    agent("Order 1057 arrived broken, I want a refund.", "--local-rules")
    assert capsys.readouterr().out.strip() == config.HANDOFF_REFUND
    assert "GET /v1/guardrails/rules" not in api.paths
    assert any(s["kind"] == "guardrail" for s in api.spans)


def test_without_rules_the_refund_goes_through(
    api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api.rules = None
    agent("Order 1057 arrived broken, I want a refund.")
    assert "refunded $489.00" in capsys.readouterr().out


def test_no_content_records_no_tool_previews(api: FakeApi) -> None:
    agent("Where's order 1042? I want a refund.", "--no-content")
    for s in api.spans:
        assert Attr.INPUT_PREVIEW not in s["attributes"]
        assert Attr.OUTPUT_PREVIEW not in s["attributes"]


def test_local_rules_equal_the_servers_built_in_rules() -> None:
    assert local_rules() == {
        "rules": [r.model_dump(mode="json", exclude_defaults=True) for r in BUILT_IN_RULES]
    }
    assert f"amount > {config.REFUND_LIMIT_USD:g}" == BUILT_IN_RULES[0].condition
    # the file parses on its own too
    text = (cli.__file__.rsplit("/", 1)[0]) + "/rules.yaml"
    with open(text, encoding="utf-8") as f:
        assert yaml.safe_load(f) == local_rules()


def test_preview_attr_keys_match_schema() -> None:
    assert config.ATTR_INPUT_PREVIEW == Attr.INPUT_PREVIEW
    assert config.ATTR_OUTPUT_PREVIEW == Attr.OUTPUT_PREVIEW
