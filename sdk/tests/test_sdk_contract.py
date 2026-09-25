"""Contract: the SDK's duplicated constants match ``schema``, and every exported span validates.

(The ``ingest`` fixture also validates every batch any SDK test exports.)
"""

from __future__ import annotations

from typing import Any

import anthropic
import httpx2
import openai
from conftest import (
    FAKE_KEY,
    Ingest,
    Provider,
    anthropic_message,
    anthropic_stream,
    json_response,
    openai_completion,
    openai_stream_response,
    spans_of,
    sse_response,
)

import lucentpad
from lucentpad import _attrs
from lucentpad_server import schema


def test_duplicated_constants_match_schema() -> None:
    for name, value in vars(_attrs.Attr).items():
        if name.isupper():
            assert getattr(schema.Attr, name) == value, name
    assert _attrs.PREVIEW_MAX_CHARS == schema.PREVIEW_MAX_CHARS
    assert _attrs.MAX_BATCH_SPANS == schema.MAX_BATCH_SPANS
    fields = schema.Span.model_fields
    assert any(
        getattr(m, "max_length", None) == _attrs.NAME_MAX_CHARS for m in fields["name"].metadata
    )
    assert any(
        getattr(m, "max_length", None) == _attrs.STATUS_MESSAGE_MAX_CHARS
        for m in fields["status_message"].metadata
    )


@lucentpad.span
def lookup_order(order_id: str) -> dict[str, Any]:
    return {"order_id": order_id}


@lucentpad.span(name="x" * 300)
def long_named() -> None:
    raise RuntimeError("e" * 5000)


def test_every_exported_span_validates(ingest: Ingest) -> None:
    ap = Provider(
        [
            json_response(anthropic_message("a", tool=("lookup_order", {"order_id": "1"}))),
            sse_response(anthropic_stream(["b" * 3000])),
        ]
    )
    op = Provider([json_response(openai_completion("c")), openai_stream_response(["d"])])
    a = lucentpad.wrap(
        anthropic.Anthropic(
            api_key=FAKE_KEY,
            http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(ap.handler)),
        )
    )
    o = lucentpad.wrap(
        openai.OpenAI(
            api_key=FAKE_KEY,
            http_client=openai.DefaultHttpxClient(transport=httpx2.MockTransport(op.handler)),
        )
    )
    msgs: list[Any] = [{"role": "user", "content": "q"}]
    try:
        with lucentpad.trace("contract", input="i" * 5000, flag=True, ratio=0.5):
            a.messages.create(model="claude-sonnet-5", max_tokens=5, messages=msgs)
            lookup_order("1")
            list(
                a.messages.create(model="claude-sonnet-5", max_tokens=5, messages=msgs, stream=True)
            )
            o.chat.completions.create(model="gpt-5", messages=msgs)
            list(o.chat.completions.create(model="gpt-5", messages=msgs, stream=True))
            long_named()
    except RuntimeError:
        pass
    spans = spans_of(ingest, 7)
    for raw in spans:
        schema.Span.model_validate(raw)
    assert {s["kind"] for s in spans} == {"agent", "llm", "tool"}
    assert len({s["trace_id"] for s in spans}) == 1
    long = next(s for s in spans if s["name"].startswith("xxx"))
    assert len(long["name"]) == 200 and len(long["status_message"]) == 2000
    assert not ingest.invalid
