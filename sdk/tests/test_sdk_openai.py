"""OpenAI wrapper: sync, async, streaming with D4 (usage requested, then hidden), tool calls."""

from __future__ import annotations

from typing import Any

import httpx2
import openai
from conftest import (
    FAKE_KEY,
    Ingest,
    Provider,
    json_response,
    openai_chunks,
    openai_completion,
    openai_stream_response,
    spans_of,
    sse_response,
)

import lucentpad
from lucentpad_server.schema import Attr

MODEL = "gpt-5"
MSGS: list[Any] = [{"role": "user", "content": "Where's order 1042?"}]


def sync_client(provider: Provider) -> openai.OpenAI:
    return openai.OpenAI(
        api_key=FAKE_KEY,
        max_retries=0,
        http_client=openai.DefaultHttpxClient(transport=httpx2.MockTransport(provider.handler)),
    )


def async_client(provider: Provider) -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(
        api_key=FAKE_KEY,
        max_retries=0,
        http_client=openai.DefaultAsyncHttpxClient(
            transport=httpx2.MockTransport(provider.handler)
        ),
    )


def assert_llm_span(span: dict[str, Any], *, streaming: bool, tokens: tuple[int, int]) -> None:
    a = span["attributes"]
    assert span["kind"] == "llm" and span["name"] == f"chat {MODEL}"
    assert a[Attr.GEN_AI_SYSTEM] == "openai"
    assert a[Attr.GEN_AI_REQUEST_MODEL] == MODEL
    assert a[Attr.GEN_AI_RESPONSE_MODEL] == "gpt-5-2026-08-01"
    assert (a[Attr.GEN_AI_INPUT_TOKENS], a[Attr.GEN_AI_OUTPUT_TOKENS]) == tokens
    assert a[Attr.STREAMING] is streaming
    assert a[Attr.CLIENT] == "sdk"


def test_sync_create(ingest: Ingest) -> None:
    provider = Provider([json_response(openai_completion("It shipped."))])
    client = lucentpad.wrap(sync_client(provider))
    out = client.chat.completions.create(model=MODEL, messages=MSGS)
    assert out.choices[0].message.content == "It shipped."
    (span,) = spans_of(ingest, 1)
    assert_llm_span(span, streaming=False, tokens=(40, 9))
    a = span["attributes"]
    assert a[Attr.GEN_AI_FINISH_REASONS] == ["stop"]
    assert a[Attr.INPUT_PREVIEW] == "Where's order 1042?"
    assert a[Attr.OUTPUT_PREVIEW] == "It shipped."
    assert "stream_options" not in provider.bodies()[0]


async def test_async_create_with_tool_call(ingest: Ingest) -> None:
    reply = openai_completion(None, tool=("issue_refund", '{"order_id": "1042"}'))
    provider = Provider([json_response(reply)])
    client = lucentpad.wrap(async_client(provider))
    history: list[Any] = [
        {"role": "user", "content": "refund please"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_0",
                    "type": "function",
                    "function": {"name": "lookup_order", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_0", "content": '{"status": "shipped"}'},
    ]
    await client.chat.completions.create(model=MODEL, messages=history)
    (span,) = spans_of(ingest, 1)
    assert_llm_span(span, streaming=False, tokens=(40, 9))
    a = span["attributes"]
    assert a[Attr.GEN_AI_FINISH_REASONS] == ["tool_calls"]
    assert a[Attr.INPUT_PREVIEW] == "[tool_result lookup_order]"
    assert a[Attr.OUTPUT_PREVIEW] == "[tool_use issue_refund]"


def test_stream_injects_include_usage_and_hides_usage_chunk(ingest: Ingest) -> None:
    plain_provider = Provider([openai_stream_response(["Hel", "lo"])])
    traced_provider = Provider([openai_stream_response(["Hel", "lo"])])
    plain = sync_client(plain_provider)
    traced = lucentpad.wrap(sync_client(traced_provider))
    expected = [
        c.model_dump_json()
        for c in plain.chat.completions.create(model=MODEL, messages=MSGS, stream=True)
    ]
    stream = traced.chat.completions.create(model=MODEL, messages=MSGS, stream=True)
    assert isinstance(stream, openai.Stream)
    got = [c.model_dump_json() for c in stream]
    assert got == expected  # the usage-only chunk the SDK asked for is not shown
    assert "stream_options" not in plain_provider.bodies()[0]
    assert traced_provider.bodies()[0]["stream_options"] == {"include_usage": True}
    (span,) = spans_of(ingest, 1)
    assert_llm_span(span, streaming=True, tokens=(44, 6))
    assert span["attributes"][Attr.OUTPUT_PREVIEW] == "Hello"
    assert span["attributes"][Attr.GEN_AI_FINISH_REASONS] == ["stop"]


def test_stream_keeps_usage_chunk_when_caller_asked_for_it(ingest: Ingest) -> None:
    provider = Provider([openai_stream_response(["a"])])
    client = lucentpad.wrap(sync_client(provider))
    chunks = list(
        client.chat.completions.create(
            model=MODEL, messages=MSGS, stream=True, stream_options={"include_usage": True}
        )
    )
    assert chunks[-1].choices == [] and chunks[-1].usage is not None
    assert chunks[-1].usage.prompt_tokens == 44
    (span,) = spans_of(ingest, 1)
    assert_llm_span(span, streaming=True, tokens=(44, 6))


def test_stream_respects_explicit_include_usage_false(ingest: Ingest) -> None:
    provider = Provider([openai_stream_response(["a"])])
    client = lucentpad.wrap(sync_client(provider))
    list(
        client.chat.completions.create(
            model=MODEL, messages=MSGS, stream=True, stream_options={"include_usage": False}
        )
    )
    assert provider.bodies()[0]["stream_options"] == {"include_usage": False}
    (span,) = spans_of(ingest, 1)
    assert Attr.GEN_AI_INPUT_TOKENS not in span["attributes"]


async def test_async_stream_with_tool_call(ingest: Ingest) -> None:
    provider = Provider([openai_stream_response(["Checking"], tool="lookup_order")])
    client = lucentpad.wrap(async_client(provider))
    stream = await client.chat.completions.create(model=MODEL, messages=MSGS, stream=True)
    chunks = [c async for c in stream]
    assert all(c.choices for c in chunks)
    (span,) = spans_of(ingest, 1)
    assert_llm_span(span, streaming=True, tokens=(44, 6))
    a = span["attributes"]
    assert a[Attr.OUTPUT_PREVIEW] == "Checking\n[tool_use lookup_order]"
    assert a[Attr.GEN_AI_FINISH_REASONS] == ["tool_calls"]


def test_stream_without_usage_from_server_still_ends(ingest: Ingest) -> None:
    provider = Provider([sse_response(openai_chunks(["x"]))])
    client = lucentpad.wrap(sync_client(provider))
    assert len(list(client.chat.completions.create(model=MODEL, messages=MSGS, stream=True))) == 3
    (span,) = spans_of(ingest, 1)
    assert span["attributes"][Attr.OUTPUT_PREVIEW] == "x"


def test_http_error(ingest: Ingest) -> None:
    provider = Provider([json_response({"error": {"message": "rate limited"}}, status=429)])
    client = lucentpad.wrap(sync_client(provider))
    try:
        client.chat.completions.create(model=MODEL, messages=MSGS)
    except openai.RateLimitError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected RateLimitError")
    (span,) = spans_of(ingest, 1)
    assert span["status"] == "error"
