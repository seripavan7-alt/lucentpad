"""The demo agent with --mock-llm, verified against a mocked LucentPad ingest endpoint."""

from __future__ import annotations

import json
import threading
from typing import Any

import httpx
import pytest

import lucentpad
from lucentpad import _core
from lucentpad._exporter import Exporter
from lucentpad_server.schema import Attr, SpanBatch
from support_agent import __main__ as cli
from support_agent import config
from support_agent.agent import run
from support_agent.mock_llm import mock_client
from support_agent.tools import run_tool


class FakeIngest:
    def __init__(self) -> None:
        self.spans: list[dict[str, Any]] = []
        self.endpoints: list[str] = []
        self.lock = threading.Lock()

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        SpanBatch.model_validate(body)  # the contract
        with self.lock:
            self.spans.extend(body["spans"])
        return httpx.Response(202, json={"accepted": len(body["spans"])})


@pytest.fixture
def ingest(monkeypatch: pytest.MonkeyPatch) -> Any:
    fake = FakeIngest()

    def exporter(endpoint: str) -> Exporter:
        fake.endpoints.append(endpoint)
        return Exporter(endpoint, transport=httpx.MockTransport(fake.handler), interval=0.01)

    monkeypatch.setattr(_core, "Exporter", exporter)
    yield fake
    lucentpad.shutdown()


def by_start(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(spans, key=lambda s: (s["start_time"], s["kind"] != "agent"))


def test_mock_run_emits_the_demo_waterfall(
    ingest: FakeIngest, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["--mock-llm", "--mock-delay", "0", "--endpoint", "http://lucentpad.test:8000"])
    out, err = capsys.readouterr()
    assert ingest.endpoints == ["http://lucentpad.test:8000"]
    assert "refunded $77.00 for order 1042" in out
    assert "warning" not in err

    spans = by_start(ingest.spans)
    root = spans[0]
    assert root["kind"] == "agent" and root["name"] == config.RUN_NAME
    assert root["parent_span_id"] is None
    children = spans[1:]
    assert [(s["kind"], s["name"]) for s in children] == [
        ("llm", "chat claude-sonnet-5"),
        ("tool", "lookup_order"),
        ("llm", "chat claude-sonnet-5"),
        ("tool", "issue_refund"),
        ("tool", "draft_email"),
        ("llm", "chat claude-sonnet-5"),
    ]
    assert all(s["parent_span_id"] == root["span_id"] for s in children)
    assert len({s["trace_id"] for s in spans}) == 1
    assert all(s["status"] == "ok" for s in spans)
    assert all(s["attributes"][Attr.SERVICE_NAME] == "support-agent" for s in spans)
    assert root["span_id"] == ingest.spans[-1]["span_id"]  # exported when it ends: last

    llm = [s for s in children if s["kind"] == "llm"]
    for s in llm:
        a = s["attributes"]
        assert a[Attr.GEN_AI_SYSTEM] == "anthropic"
        assert a[Attr.GEN_AI_REQUEST_MODEL] == config.MODEL
        assert a[Attr.GEN_AI_INPUT_TOKENS] > 0 and a[Attr.GEN_AI_OUTPUT_TOKENS] > 0
        assert a[Attr.CLIENT] == "sdk"
    assert [s["attributes"][Attr.GEN_AI_FINISH_REASONS] for s in llm] == [
        ["tool_use"],
        ["tool_use"],
        ["end_turn"],
    ]
    assert llm[0]["attributes"][Attr.INPUT_PREVIEW] == config.DEFAULT_QUESTION
    assert llm[1]["attributes"][Attr.INPUT_PREVIEW] == "[tool_result lookup_order]"
    assert llm[1]["attributes"][Attr.OUTPUT_PREVIEW].endswith(
        "[tool_use issue_refund]\n[tool_use draft_email]"
    )
    assert root["attributes"][Attr.INPUT_PREVIEW] == config.DEFAULT_QUESTION
    assert root["attributes"][Attr.OUTPUT_PREVIEW].startswith("Done! I've refunded $77.00")
    refund = next(s for s in children if s["name"] == "issue_refund")
    assert refund["attributes"][Attr.REFUND_AMOUNT] == 77.0
    assert refund["attributes"][Attr.GEN_AI_TOOL_NAME] == "issue_refund"


def test_unknown_order_is_a_tool_error(ingest: FakeIngest) -> None:
    lucentpad.init("http://lucentpad.test", service_name=config.SERVICE_NAME)
    client = lucentpad.wrap(mock_client())
    result = run(client, "Where's order 9999?")
    assert lucentpad.flush(3.0)
    assert "couldn't find" in result.answer
    spans = by_start(ingest.spans)
    assert [s["name"] for s in spans[1:]] == [
        "chat claude-sonnet-5",
        "lookup_order",
        "chat claude-sonnet-5",
    ]
    assert spans[2]["status"] == "error"
    assert spans[2]["status_message"] == "ToolError: order 9999 not found"


def test_status_question_does_not_refund(ingest: FakeIngest) -> None:
    lucentpad.init("http://lucentpad.test")
    result = run(lucentpad.wrap(mock_client()), "Where is order 1043?")
    assert result.answer == "Order 1043 is shipped; it was placed on 2026-09-20."
    assert result.turns == 2


def test_tools_validate_inputs() -> None:
    _, err = run_tool("issue_refund", {"order_id": "1042", "amount": 5000})
    assert err
    _, err = run_tool("draft_email", {"to": "nobody", "subject": "s", "body": "b"})
    assert err
    _, err = run_tool("nope", {})
    assert err
    content, err = run_tool("lookup_order", {"order_id": "#1042"})
    assert not err and json.loads(content)["customer"]["email"] == "maya.patel@example.com"


def test_live_mode_needs_a_key_and_never_prints_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY is not set"):
        cli.build_client(mock=False, mock_delay=0)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-value")
    client = cli.build_client(mock=False, mock_delay=0)  # built, but never called in tests
    assert "sk-ant-test-value" not in repr(client)


def test_refund_attr_key_matches_schema() -> None:
    assert config.ATTR_REFUND_AMOUNT == Attr.REFUND_AMOUNT
