"""Shared fixtures for SDK tests: a mocked ingest endpoint and mocked provider HTTP.

No network, no real keys: providers are ``httpx2.MockTransport`` (the transport stack anthropic
and openai are built on); the ingest endpoint is an ``httpx.MockTransport`` on the exporter.
Every span the SDK exports is validated against ``lucentpad_server.schema`` (the contract).
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import httpx2
import pytest
from pydantic import ValidationError

import lucentpad
from lucentpad import _core
from lucentpad._exporter import Exporter
from lucentpad_server.schema import SpanBatch

FAKE_KEY = "sk-test-not-a-real-key"


@dataclass
class Ingest:
    """A fake ``POST /v1/spans``: records batches, validates each against the contract."""

    spans: list[dict[str, Any]] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)
    status: int = 202
    headers: dict[str, str] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def handler(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            self.requests.append(request)
            status = self.status
        if status != 202:
            return httpx.Response(status, headers=self.headers, json={"detail": "nope"})
        body = json.loads(request.content)
        try:
            SpanBatch.model_validate(body)
        except ValidationError as exc:
            self.invalid.append(str(exc))
        with self.lock:
            self.spans.extend(body["spans"])
        return httpx.Response(202, json={"accepted": len(body["spans"])})

    def wait_for(self, n: int, timeout: float = 3.0) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if len(self.spans) >= n:
                    return list(self.spans)
            time.sleep(0.01)
        with self.lock:
            raise AssertionError(f"expected {n} spans, got {len(self.spans)}: {self.names()}")

    def names(self) -> list[str]:
        return [s["name"] for s in self.spans]

    def by_name(self, name: str) -> dict[str, Any]:
        matches = [s for s in self.spans if s["name"] == name]
        assert len(matches) == 1, (name, self.names())
        return matches[0]


def start_sdk(
    ingest: Ingest,
    *,
    capture_content: bool = True,
    service_name: str | None = "test-svc",
    **exporter_kwargs: Any,
) -> Exporter:
    exporter_kwargs.setdefault("interval", 0.01)
    exporter = Exporter(
        "http://ingest.test", transport=httpx.MockTransport(ingest.handler), **exporter_kwargs
    )
    _core.configure(
        "http://ingest.test",
        service_name=service_name,
        capture_content=capture_content,
        enabled=True,
        exporter=exporter,
    )
    return exporter


@pytest.fixture
def ingest() -> Iterator[Ingest]:
    fake = Ingest()
    start_sdk(fake)
    yield fake
    lucentpad.flush(2.0)
    lucentpad.shutdown()
    assert not fake.invalid, fake.invalid
    for request in fake.requests:  # the exporter sends no credentials
        assert "authorization" not in request.headers
        assert "x-api-key" not in request.headers
        assert FAKE_KEY not in request.content.decode()


@pytest.fixture(autouse=True)
def _reset_sdk() -> Iterator[None]:
    yield
    lucentpad.shutdown()


def spans_of(ingest: Ingest, n: int) -> list[dict[str, Any]]:
    assert lucentpad.flush(3.0)
    return ingest.wait_for(n)


# --------------------------------------------------------------------------- provider fakes


def sse(events: list[tuple[str | None, dict[str, Any] | str]]) -> list[bytes]:
    out = []
    for name, data in events:
        payload = data if isinstance(data, str) else json.dumps(data)
        prefix = f"event: {name}\n" if name else ""
        out.append(f"{prefix}data: {payload}\n\n".encode())
    return out


Handler = Callable[[httpx2.Request], httpx2.Response]


@dataclass
class Provider:
    """Scripted provider: each request pops the next response factory."""

    responses: list[Callable[[httpx2.Request], httpx2.Response]]
    requests: list[httpx2.Request] = field(default_factory=list)

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.responses.pop(0)(request)

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests]


def json_response(body: dict[str, Any], status: int = 200) -> Callable[..., httpx2.Response]:
    return lambda _req: httpx2.Response(status, json=body)


def sse_response(chunks: list[bytes]) -> Callable[..., httpx2.Response]:
    return lambda _req: httpx2.Response(
        200, headers={"content-type": "text/event-stream"}, content=b"".join(chunks)
    )


def anthropic_message(
    text: str = "Hello there",
    *,
    model: str = "claude-sonnet-5-20260901",
    tool: tuple[str, dict[str, Any]] | None = None,
    stop: str = "end_turn",
    usage: tuple[int, int] = (25, 12),
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
    if tool is not None:
        content.append({"type": "tool_use", "id": "toolu_01", "name": tool[0], "input": tool[1]})
    return {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": "tool_use" if tool else stop,
        "stop_sequence": None,
        "usage": {"input_tokens": usage[0], "output_tokens": usage[1]},
    }


def anthropic_stream(
    texts: list[str],
    *,
    model: str = "claude-sonnet-5-20260901",
    tool: str | None = None,
    usage: tuple[int, int] = (31, 17),
    error: bool = False,
) -> list[bytes]:
    events: list[tuple[str | None, dict[str, Any] | str]] = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_02",
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": usage[0], "output_tokens": 1},
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        ("ping", {"type": "ping"}),
    ]
    for t in texts:
        events.append(
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": t},
                },
            )
        )
    if error:
        events.append(
            ("error", {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}})
        )
        return sse(events)
    events.append(("content_block_stop", {"type": "content_block_stop", "index": 0}))
    if tool:
        events += [
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {
                        "type": "tool_use",
                        "id": "toolu_9",
                        "name": tool,
                        "input": {},
                    },
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "input_json_delta", "partial_json": '{"order_id": "1042"}'},
                },
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": 1}),
        ]
    events += [
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use" if tool else "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": usage[1]},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    return sse(events)


def openai_completion(
    text: str | None = "Hi!",
    *,
    model: str = "gpt-5-2026-08-01",
    tool: tuple[str, str] | None = None,
    usage: tuple[int, int] = (40, 9),
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if tool is not None:
        message["tool_calls"] = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": tool[0], "arguments": tool[1]},
            }
        ]
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1_790_000_000,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool else "stop",
            }
        ],
        "usage": {
            "prompt_tokens": usage[0],
            "completion_tokens": usage[1],
            "total_tokens": sum(usage),
        },
    }


def openai_stream_response(
    texts: list[str],
    *,
    model: str = "gpt-5-2026-08-01",
    tool: str | None = None,
    usage: tuple[int, int] = (44, 6),
) -> Callable[[httpx2.Request], httpx2.Response]:
    """Like the real API: the usage-only chunk is sent only when the request asks for it."""

    def respond(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
        return sse_response(
            openai_chunks(texts, model=model, tool=tool, usage=usage if include_usage else None)
        )(request)

    return respond


def openai_chunks(
    texts: list[str],
    *,
    model: str = "gpt-5-2026-08-01",
    tool: str | None = None,
    usage: tuple[int, int] | None = None,
) -> list[bytes]:
    def chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
        return {
            "id": "chatcmpl-2",
            "object": "chat.completion.chunk",
            "created": 1_790_000_000,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }

    events: list[tuple[str | None, dict[str, Any] | str]] = [
        (None, chunk({"role": "assistant", "content": ""}))
    ]
    events += [(None, chunk({"content": t})) for t in texts]
    if tool:
        events.append(
            (
                None,
                chunk(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_7",
                                "type": "function",
                                "function": {"name": tool, "arguments": ""},
                            }
                        ]
                    }
                ),
            )
        )
        events.append(
            (
                None,
                chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"a": 1}'}}]}),
            )
        )
    events.append((None, chunk({}, "tool_calls" if tool else "stop")))
    if usage is not None:
        events.append(
            (
                None,
                {
                    "id": "chatcmpl-2",
                    "object": "chat.completion.chunk",
                    "created": 1_790_000_000,
                    "model": model,
                    "choices": [],
                    "usage": {
                        "prompt_tokens": usage[0],
                        "completion_tokens": usage[1],
                        "total_tokens": sum(usage),
                    },
                },
            )
        )
    events.append((None, "[DONE]"))
    return sse(events)
