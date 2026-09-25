"""trace() roots, @span children, contextvars parenting, root previews, disabled mode."""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import threading

import anthropic
import httpx2
import pytest
from conftest import (
    FAKE_KEY,
    Ingest,
    Provider,
    anthropic_message,
    json_response,
    spans_of,
    start_sdk,
)

import lucentpad
from lucentpad import _core
from lucentpad_server.schema import Attr


def client_for(provider: Provider) -> anthropic.Anthropic:
    return lucentpad.wrap(
        anthropic.Anthropic(
            api_key=FAKE_KEY,
            max_retries=0,
            http_client=anthropic.DefaultHttpxClient(
                transport=httpx2.MockTransport(provider.handler)
            ),
        )
    )


@lucentpad.span
def outer_tool(x: int) -> int:
    return inner_step(x) + 1


@lucentpad.span(name="inner", kind="agent")
def inner_step(x: int) -> int:
    lucentpad.set_attribute("lucentpad.refund.amount", 42)
    return x * 2


@lucentpad.span
async def async_tool(x: int) -> int:
    await asyncio.sleep(0)
    return x


@lucentpad.span
def failing_tool() -> None:
    raise ValueError("order 9 not found")


def test_trace_with_nested_spans(ingest: Ingest) -> None:
    with lucentpad.trace("support-agent.run", customer="c1") as t:
        assert outer_tool(3) == 7
    spans = spans_of(ingest, 3)
    assert [s["name"] for s in spans] == ["inner", "outer_tool", "support-agent.run"]  # end order
    inner, outer, root = spans
    assert root["kind"] == "agent" and root["parent_span_id"] is None
    assert root["trace_id"] == t.trace_id
    assert root["attributes"]["customer"] == "c1"
    assert root["attributes"][Attr.SERVICE_NAME] == "test-svc"
    assert outer["kind"] == "tool" and outer["parent_span_id"] == root["span_id"]
    assert outer["attributes"][Attr.GEN_AI_TOOL_NAME] == "outer_tool"
    assert outer["attributes"][Attr.GEN_AI_OPERATION] == "execute_tool"
    assert inner["kind"] == "agent" and inner["parent_span_id"] == outer["span_id"]
    assert inner["attributes"][Attr.REFUND_AMOUNT] == 42
    assert {s["trace_id"] for s in spans} == {t.trace_id}
    assert len(t.trace_id) == 32 and len(root["span_id"]) == 16


async def test_async_trace_and_span(ingest: Ingest) -> None:
    async with lucentpad.trace("async-run") as t:
        results = await asyncio.gather(async_tool(1), async_tool(2))
    assert list(results) == [1, 2]
    spans = spans_of(ingest, 3)
    root = next(s for s in spans if s["kind"] == "agent")
    tools = [s for s in spans if s["kind"] == "tool"]
    assert len(tools) == 2
    assert all(s["parent_span_id"] == root["span_id"] for s in tools)
    assert all(s["trace_id"] == t.trace_id for s in spans)


def test_parenting_across_threads_with_copy_context(ingest: Ingest) -> None:
    with lucentpad.trace("threaded"):
        ctx = contextvars.copy_context()
        th = threading.Thread(target=ctx.run, args=(outer_tool, 1))
        th.start()
        th.join()
    spans = spans_of(ingest, 3)
    root = spans[-1]
    outer = next(s for s in spans if s["name"] == "outer_tool")
    assert outer["parent_span_id"] == root["span_id"]


def test_span_error_is_recorded_and_reraised(ingest: Ingest) -> None:
    with pytest.raises(ValueError, match="not found"), lucentpad.trace("run"):
        failing_tool()
    tool, root = spans_of(ingest, 2)
    assert tool["status"] == "error"
    assert tool["status_message"] == "ValueError: order 9 not found"
    assert root["status"] == "error"


def test_span_outside_trace_is_its_own_root(ingest: Ingest) -> None:
    assert outer_tool(1) == 3
    inner, outer = spans_of(ingest, 2)
    assert outer["parent_span_id"] is None
    assert inner["parent_span_id"] == outer["span_id"]


def test_root_previews_come_from_first_and_last_llm_call(ingest: Ingest) -> None:
    provider = Provider(
        [json_response(anthropic_message("first reply")), json_response(anthropic_message("final"))]
    )
    client = client_for(provider)
    with lucentpad.trace("run"):
        client.messages.create(
            model="claude-sonnet-5",
            max_tokens=5,
            messages=[{"role": "user", "content": "the question"}],
        )
        client.messages.create(
            model="claude-sonnet-5",
            max_tokens=5,
            messages=[
                {"role": "user", "content": "the question"},
                {"role": "assistant", "content": "first reply"},
                {"role": "user", "content": "follow-up"},
            ],
        )
    spans = spans_of(ingest, 3)
    root = spans[-1]
    assert root["attributes"][Attr.INPUT_PREVIEW] == "the question"
    assert root["attributes"][Attr.OUTPUT_PREVIEW] == "final"
    assert root["attributes"][Attr.OUTPUT_TRUNCATED] is False
    assert all(s["parent_span_id"] == root["span_id"] for s in spans[:2])


def test_explicit_root_previews_win(ingest: Ingest) -> None:
    provider = Provider([json_response(anthropic_message("model text"))])
    client = client_for(provider)
    with lucentpad.trace("run", input="given input") as t:
        client.messages.create(
            model="claude-sonnet-5", max_tokens=5, messages=[{"role": "user", "content": "q"}]
        )
        t.set_output("x" * 2500)
    root = spans_of(ingest, 2)[-1]
    a = root["attributes"]
    assert a[Attr.INPUT_PREVIEW] == "given input" and a[Attr.INPUT_TRUNCATED] is False
    assert a[Attr.OUTPUT_PREVIEW] == "x" * 2000 and a[Attr.OUTPUT_TRUNCATED] is True


def test_non_primitive_attributes_are_coerced(ingest: Ingest) -> None:
    with lucentpad.trace("run", tags=("a", "b"), obj={"k": 1}, n=3):
        pass
    (root,) = spans_of(ingest, 1)
    assert root["attributes"]["tags"] == ["a", "b"]
    assert root["attributes"]["obj"] == "{'k': 1}"
    assert root["attributes"]["n"] == 3


@pytest.mark.parametrize("how", ["flag", "env"])
def test_disabled_is_a_noop(how: str, monkeypatch: pytest.MonkeyPatch) -> None:
    if how == "env":
        monkeypatch.setenv("LUCENTPAD_DISABLED", "1")
        lucentpad.init("http://127.0.0.1:9")
    else:
        lucentpad.init("http://127.0.0.1:9", enabled=False)
    assert _core.active() is None and _core.current_exporter() is None
    with lucentpad.trace("run") as t:
        assert outer_tool(1) == 3
        lucentpad.set_attribute("k", "v")
        t.set_output("x")
    assert lucentpad.flush(0.1) is True
    lucentpad.shutdown()


def test_endpoint_env_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUCENTPAD_ENDPOINT", "http://collector.test:9999")
    lucentpad.init(service_name="svc")
    exporter = _core.current_exporter()
    assert exporter is not None and exporter.url == "http://collector.test:9999/v1/spans"
    lucentpad.init("http://explicit.test")
    exporter = _core.current_exporter()
    assert exporter is not None and exporter.url == "http://explicit.test/v1/spans"
    lucentpad.shutdown()


def test_reinit_replaces_exporter() -> None:
    first, second = Ingest(), Ingest()
    start_sdk(first)
    with lucentpad.trace("one"):
        pass
    start_sdk(second)  # shuts the first exporter down (flushing it)
    with lucentpad.trace("two"):
        pass
    assert first.names() == ["one"]
    assert [s["name"] for s in spans_of(second, 1)] == ["two"]


def test_decorator_preserves_metadata() -> None:
    assert outer_tool.__name__ == "outer_tool"
    assert inspect.iscoroutinefunction(async_tool)
