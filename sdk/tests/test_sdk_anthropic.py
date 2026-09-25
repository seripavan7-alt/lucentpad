"""Anthropic wrapper: sync, async, streaming (both styles), tool use, errors, previews."""

from __future__ import annotations

import gc
import json
from typing import Any

import anthropic
import httpx2
import pytest
from conftest import (
    FAKE_KEY,
    Ingest,
    Provider,
    anthropic_message,
    anthropic_stream,
    json_response,
    spans_of,
    sse_response,
    start_sdk,
)

import lucentpad
from lucentpad._attrs import PREVIEW_MAX_CHARS
from lucentpad_server.schema import Attr

MODEL = "claude-sonnet-5"
MSGS: list[Any] = [{"role": "user", "content": "Where's order 1042?"}]


def sync_client(provider: Provider) -> anthropic.Anthropic:
    return anthropic.Anthropic(
        api_key=FAKE_KEY,
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(provider.handler)),
    )


def async_client(provider: Provider) -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(
        api_key=FAKE_KEY,
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(
            transport=httpx2.MockTransport(provider.handler)
        ),
    )


def assert_llm_span(span: dict[str, Any], *, streaming: bool, tokens: tuple[int, int]) -> None:
    a = span["attributes"]
    assert span["kind"] == "llm"
    assert span["source"] == "sdk"
    assert span["name"] == f"chat {MODEL}"
    assert a[Attr.GEN_AI_SYSTEM] == "anthropic"
    assert a[Attr.GEN_AI_OPERATION] == "chat"
    assert a[Attr.GEN_AI_REQUEST_MODEL] == MODEL
    assert a[Attr.GEN_AI_RESPONSE_MODEL] == "claude-sonnet-5-20260901"
    assert (a[Attr.GEN_AI_INPUT_TOKENS], a[Attr.GEN_AI_OUTPUT_TOKENS]) == tokens
    assert a[Attr.STREAMING] is streaming
    assert a[Attr.CLIENT] == "sdk"
    assert a[Attr.SERVICE_NAME] == "test-svc"
    assert Attr.COST_USD not in a  # the server prices tokens


def test_wrap_returns_same_client_and_is_idempotent() -> None:
    client = sync_client(Provider([]))
    assert lucentpad.wrap(client) is client
    first = client.messages.create
    assert lucentpad.wrap(client) is client
    assert client.messages.create is first
    other = object()
    assert lucentpad.wrap(other) is other


def test_sync_create(ingest: Ingest) -> None:
    provider = Provider([json_response(anthropic_message("It shipped yesterday."))])
    client = lucentpad.wrap(sync_client(provider))
    msg = client.messages.create(model=MODEL, max_tokens=100, messages=MSGS)
    assert msg.content[0].type == "text"
    (span,) = spans_of(ingest, 1)
    assert_llm_span(span, streaming=False, tokens=(25, 12))
    a = span["attributes"]
    assert a[Attr.GEN_AI_FINISH_REASONS] == ["end_turn"]
    assert a[Attr.INPUT_PREVIEW] == "Where's order 1042?"
    assert a[Attr.OUTPUT_PREVIEW] == "It shipped yesterday."
    assert a[Attr.INPUT_TRUNCATED] is False and a[Attr.OUTPUT_TRUNCATED] is False
    assert span["parent_span_id"] is None
    assert "stream" not in provider.bodies()[0]


async def test_async_create(ingest: Ingest) -> None:
    provider = Provider([json_response(anthropic_message("ok"))])
    client = lucentpad.wrap(async_client(provider))
    msg = await client.messages.create(model=MODEL, max_tokens=100, messages=MSGS)
    assert msg.model == "claude-sonnet-5-20260901"
    (span,) = spans_of(ingest, 1)
    assert_llm_span(span, streaming=False, tokens=(25, 12))


def test_tool_use_is_recorded_on_the_llm_span(ingest: Ingest) -> None:
    reply = anthropic_message("Let me check.", tool=("lookup_order", {"order_id": "1042"}))
    provider = Provider([json_response(reply)])
    client = lucentpad.wrap(sync_client(provider))
    history: list[Any] = [
        {"role": "user", "content": "Where's order 1042?"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "toolu_00", "name": "lookup_order", "input": {}}
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_00", "content": "{}"}],
        },
    ]
    client.messages.create(model=MODEL, max_tokens=100, messages=history)
    (span,) = spans_of(ingest, 1)
    a = span["attributes"]
    assert a[Attr.GEN_AI_FINISH_REASONS] == ["tool_use"]
    assert a[Attr.OUTPUT_PREVIEW] == "Let me check.\n[tool_use lookup_order]"
    assert a[Attr.INPUT_PREVIEW] == "[tool_result lookup_order]"


def test_create_stream_passes_chunks_through_unchanged(ingest: Ingest) -> None:
    chunks = anthropic_stream(["Hel", "lo ", "world"])
    plain = sync_client(Provider([sse_response(chunks)]))
    traced = lucentpad.wrap(sync_client(Provider([sse_response(chunks)])))
    expected = [
        e.model_dump_json()
        for e in plain.messages.create(model=MODEL, max_tokens=50, messages=MSGS, stream=True)
    ]
    stream = traced.messages.create(model=MODEL, max_tokens=50, messages=MSGS, stream=True)
    assert isinstance(stream, anthropic.Stream)
    got = [e.model_dump_json() for e in stream]
    assert got == expected
    (span,) = spans_of(ingest, 1)
    assert_llm_span(span, streaming=True, tokens=(31, 17))
    assert span["attributes"][Attr.OUTPUT_PREVIEW] == "Hello world"
    assert span["attributes"][Attr.GEN_AI_FINISH_REASONS] == ["end_turn"]


def test_stream_chunks_arrive_as_they_are_produced(ingest: Ingest) -> None:
    chunks = anthropic_stream(["a", "b", "c", "d"])
    produced: list[int] = []

    def body() -> Any:
        for i, c in enumerate(chunks):
            produced.append(i)
            yield c

    provider = Provider(
        [
            lambda _r: httpx2.Response(
                200, headers={"content-type": "text/event-stream"}, content=body()
            )
        ]
    )
    client = lucentpad.wrap(sync_client(provider))
    seen = 0
    for event in client.messages.create(model=MODEL, max_tokens=5, messages=MSGS, stream=True):
        if event.type == "content_block_delta":
            seen += 1
            # the SDK has not read ahead: the producer is at most one SSE event past this one
            assert len(produced) <= 4 + seen
    assert seen == 4
    spans_of(ingest, 1)


async def test_async_create_stream(ingest: Ingest) -> None:
    provider = Provider([sse_response(anthropic_stream(["x", "y"], tool="lookup_order"))])
    client = lucentpad.wrap(async_client(provider))
    stream = await client.messages.create(model=MODEL, max_tokens=50, messages=MSGS, stream=True)
    types = [e.type async for e in stream]
    assert types[0] == "message_start" and types[-1] == "message_stop"
    (span,) = spans_of(ingest, 1)
    assert_llm_span(span, streaming=True, tokens=(31, 17))
    assert span["attributes"][Attr.OUTPUT_PREVIEW] == "xy\n[tool_use lookup_order]"
    assert span["attributes"][Attr.GEN_AI_FINISH_REASONS] == ["tool_use"]


def test_messages_stream_helper(ingest: Ingest) -> None:
    provider = Provider([sse_response(anthropic_stream(["Your ", "refund"]))])
    client = lucentpad.wrap(sync_client(provider))
    with client.messages.stream(model=MODEL, max_tokens=50, messages=MSGS) as stream:
        text = "".join(stream.text_stream)
        final = stream.get_final_message()
    assert text == "Your refund"
    assert final.usage.output_tokens == 17
    (span,) = spans_of(ingest, 1)
    assert_llm_span(span, streaming=True, tokens=(31, 17))
    assert span["attributes"][Attr.OUTPUT_PREVIEW] == "Your refund"


async def test_async_messages_stream_helper(ingest: Ingest) -> None:
    provider = Provider([sse_response(anthropic_stream(["a", "b"]))])
    client = lucentpad.wrap(async_client(provider))
    async with client.messages.stream(model=MODEL, max_tokens=50, messages=MSGS) as stream:
        text = [t async for t in stream.text_stream]
        final = await stream.get_final_message()
    assert text == ["a", "b"]
    assert final.stop_reason == "end_turn"
    (span,) = spans_of(ingest, 1)
    assert_llm_span(span, streaming=True, tokens=(31, 17))


def test_stream_closed_early_records_usage_so_far(ingest: Ingest) -> None:
    provider = Provider([sse_response(anthropic_stream(["one", "two", "three"]))])
    client = lucentpad.wrap(sync_client(provider))
    stream = client.messages.create(model=MODEL, max_tokens=50, messages=MSGS, stream=True)
    for event in stream:
        if event.type == "content_block_delta":
            break
    stream.close()
    (span,) = spans_of(ingest, 1)
    a = span["attributes"]
    assert span["status"] == "ok"
    assert a[Attr.GEN_AI_INPUT_TOKENS] == 31
    assert a[Attr.OUTPUT_PREVIEW] == "one"


def test_abandoned_stream_still_ends_its_span(ingest: Ingest) -> None:
    provider = Provider([sse_response(anthropic_stream(["one"]))])
    client = lucentpad.wrap(sync_client(provider))
    stream = client.messages.create(model=MODEL, max_tokens=50, messages=MSGS, stream=True)
    next(iter(stream))
    del stream
    gc.collect()
    spans_of(ingest, 1)


def test_stream_error_marks_span_and_propagates(ingest: Ingest) -> None:
    provider = Provider([sse_response(anthropic_stream(["partial"], error=True))])
    client = lucentpad.wrap(sync_client(provider))
    with pytest.raises(anthropic.APIStatusError):
        for _ in client.messages.create(model=MODEL, max_tokens=50, messages=MSGS, stream=True):
            pass
    (span,) = spans_of(ingest, 1)
    assert span["status"] == "error"
    assert span["attributes"][Attr.OUTPUT_PREVIEW] == "partial"


def test_http_error_marks_span_and_propagates(ingest: Ingest) -> None:
    error = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
    provider = Provider([json_response(error, status=529)])
    client = lucentpad.wrap(sync_client(provider))
    with pytest.raises(anthropic.APIStatusError):
        client.messages.create(model=MODEL, max_tokens=5, messages=MSGS)
    (span,) = spans_of(ingest, 1)
    assert span["status"] == "error"
    assert "529" in span["status_message"]
    assert FAKE_KEY not in json.dumps(span)


def test_previews_are_truncated_with_flag(ingest: Ingest) -> None:
    long_in = "q" * (PREVIEW_MAX_CHARS + 50)
    provider = Provider([sse_response(anthropic_stream(["z" * 1500, "z" * 1500]))])
    client = lucentpad.wrap(sync_client(provider))
    msgs: list[Any] = [{"role": "user", "content": [{"type": "text", "text": long_in}]}]
    events = list(client.messages.create(model=MODEL, max_tokens=5, messages=msgs, stream=True))
    texts = [
        e.delta.text
        for e in events
        if e.type == "content_block_delta" and e.delta.type == "text_delta"
    ]
    assert len("".join(texts)) == 3000
    (span,) = spans_of(ingest, 1)
    a = span["attributes"]
    assert a[Attr.INPUT_PREVIEW] == "q" * PREVIEW_MAX_CHARS and a[Attr.INPUT_TRUNCATED] is True
    assert a[Attr.OUTPUT_PREVIEW] == "z" * PREVIEW_MAX_CHARS and a[Attr.OUTPUT_TRUNCATED] is True


def test_capture_content_false_emits_no_previews() -> None:
    fake = Ingest()
    start_sdk(fake, capture_content=False)
    provider = Provider(
        [json_response(anthropic_message("secret answer")), sse_response(anthropic_stream(["s"]))]
    )
    client = lucentpad.wrap(sync_client(provider))
    with lucentpad.trace("run", input="explicit input") as t:
        client.messages.create(model=MODEL, max_tokens=5, messages=MSGS)
        list(client.messages.create(model=MODEL, max_tokens=5, messages=MSGS, stream=True))
        t.set_output("explicit output")
    spans = spans_of(fake, 3)
    for span in spans:
        for key in (
            Attr.INPUT_PREVIEW,
            Attr.OUTPUT_PREVIEW,
            Attr.INPUT_TRUNCATED,
            Attr.OUTPUT_TRUNCATED,
        ):
            assert key not in span["attributes"], (span["name"], key)
    assert spans[0]["attributes"][Attr.GEN_AI_INPUT_TOKENS] == 25


def test_not_initialised_is_a_passthrough() -> None:
    provider = Provider([json_response(anthropic_message("hi"))])
    client = lucentpad.wrap(sync_client(provider))
    assert client.messages.create(model=MODEL, max_tokens=5, messages=MSGS).content
