"""Fakes for gateway tests: an upstream behind ``httpx.MockTransport``, a recording ingest, a raw
ASGI driver (httpx's ASGITransport buffers whole responses, so timing and disconnect tests
talk ASGI directly), and canned Anthropic / OpenAI streams. Nothing touches the network."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import FastAPI

from lucentpad_server.app import create_app
from lucentpad_server.gateway.config import GatewayConfig
from lucentpad_server.gateway.proxy import HttpGatewayProxy
from lucentpad_server.schema import IngestStats, Span

API_KEY = "sk-ant-api03-SECRET-test-key-0123456789"
OPENAI_KEY = "sk-proj-SECRET-openai-test-key-987654"


class FakeIngest:
    def __init__(self, accept: bool = True) -> None:
        self.spans: list[Span] = []
        self.accept = accept

    def offer(self, spans: Sequence[Span]) -> bool:
        if not self.accept:
            return False
        self.spans.extend(spans)
        return True

    def stats(self) -> IngestStats:
        n = len(self.spans)
        return IngestStats(
            queue_depth=0,
            queue_capacity=1,
            accepted_total=n,
            rejected_total=0,
            written_total=n,
            write_errors_total=0,
        )

    def llm(self) -> list[Span]:
        return [s for s in self.spans if s.kind == "llm"]

    def roots(self) -> list[Span]:
        return [s for s in self.spans if s.kind == "agent"]


class FakeStream(httpx.AsyncByteStream):
    """Upstream body: yields ``chunks``, sleeping ``delay`` seconds before each one after the
    first; notes when it finished and whether it was closed."""

    def __init__(self, chunks: Sequence[bytes], delay: float = 0.0) -> None:
        self.chunks = list(chunks)
        self.delay = delay
        self.closed = False
        self.finished_at: float | None = None
        self.sent = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for i, chunk in enumerate(self.chunks):
            if i and self.delay:
                await asyncio.sleep(self.delay)
            self.sent += 1
            yield chunk
        self.finished_at = time.perf_counter()

    async def aclose(self) -> None:
        self.closed = True


Reply = Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]]


@dataclass
class FakeUpstream:
    """Answers each request with the next reply (the last one repeats)."""

    replies: list[Reply]
    requests: list[httpx.Request] = field(default_factory=list)
    bodies: list[bytes] = field(default_factory=list)

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.bodies.append(await request.aread())
        reply = self.replies[min(len(self.requests) - 1, len(self.replies) - 1)]
        result = reply(request)
        if isinstance(result, httpx.Response):
            return result
        return await result

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def sse_reply(stream: FakeStream, status: int = 200) -> Reply:
    return lambda _r: httpx.Response(
        status, headers={"content-type": "text/event-stream", "request-id": "req_1"}, stream=stream
    )


def json_reply(payload: dict[str, Any], status: int = 200, **headers: str) -> Reply:
    body = json.dumps(payload).encode()
    return lambda _r: httpx.Response(
        status,
        headers={
            "content-type": "application/json",
            "content-length": str(len(body)),
            "request-id": "req_1",
            **headers,
        },
        stream=FakeStream([body]),  # a real transport's body is a stream, not pre-read bytes
    )


def make_gateway(
    upstream: FakeUpstream, ingest: FakeIngest | None = None, **config: Any
) -> tuple[FastAPI, HttpGatewayProxy, FakeIngest]:
    """The real app (routes and all) with a gateway on a fake upstream; no DB, no lifespan."""
    ingest = ingest or FakeIngest()
    settings = {
        "anthropic_upstream": "https://anthropic.test",
        "openai_upstream": "https://openai.test",
        "failover_backoff_s": 0.0,
        "key_salt": b"test-salt",
        **config,
    }
    proxy = HttpGatewayProxy(GatewayConfig(**settings), ingest, transport=upstream.transport())
    app = create_app("postgresql://unused.invalid/none", seed_sample=False)
    app.state.gateway = proxy
    return app, proxy, ingest


def asgi_client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw.test")


@dataclass
class AsgiResult:
    status: int = 0
    headers: list[tuple[bytes, bytes]] = field(default_factory=list)
    chunks: list[tuple[float, bytes]] = field(default_factory=list)
    started: float = 0.0

    @property
    def body(self) -> bytes:
        return b"".join(c for _, c in self.chunks)


async def asgi_call(
    app: FastAPI,
    method: str,
    path: str,
    *,
    headers: dict[str, str],
    body: bytes = b"",
    disconnect_after_chunks: int | None = None,
) -> AsgiResult:
    """Drive the app over raw ASGI, timestamping each body message. With
    ``disconnect_after_chunks`` the client "leaves" after receiving that many non-empty chunks."""
    result = AsgiResult(started=time.perf_counter())
    gone = asyncio.Event()
    sent_body = False

    async def receive() -> dict[str, Any]:
        nonlocal sent_body
        if not sent_body:
            sent_body = True
            return {"type": "http.request", "body": body, "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message: MutableMapping[str, Any]) -> None:
        if message["type"] == "http.response.start":
            result.status = message["status"]
            result.headers = list(message.get("headers", []))
        elif message["type"] == "http.response.body":
            if message.get("body"):
                result.chunks.append((time.perf_counter(), message["body"]))
                if (
                    disconnect_after_chunks is not None
                    and len(result.chunks) >= disconnect_after_chunks
                ):
                    gone.set()
            if not message.get("more_body", False):
                gone.set()

    path_part, _, query = path.partition("?")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path_part,
        "raw_path": path_part.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("127.0.0.1", 50000),
        "server": ("gw.test", 80),
        "state": {},
    }
    await app(scope, receive, send)
    return result


def chop(data: bytes, sizes: Sequence[int] = (7, 50, 3, 120, 1, 64)) -> list[bytes]:
    """Split ``data`` at awkward places (mid-line, mid-UTF-8) to exercise the side parser."""
    out: list[bytes] = []
    i = 0
    k = 0
    while i < len(data):
        n = sizes[k % len(sizes)]
        out.append(data[i : i + n])
        i += n
        k += 1
    return out


def _sse(event: str | None, data: dict[str, Any] | str) -> str:
    payload = data if isinstance(data, str) else json.dumps(data)
    return (f"event: {event}\n" if event else "") + f"data: {payload}\n\n"


ANTHROPIC_STREAM = "".join(
    [
        _sse(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-sonnet-5",
                    "content": [],
                    "stop_reason": None,
                    "usage": {"input_tokens": 1200, "output_tokens": 1},
                },
            },
        ),
        _sse(
            "content_block_start",
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        ),
        _sse("ping", {"type": "ping"}),
        ": keep-alive comment\n\n",
        _sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Let me read the file — ünïcode "},
            },
        ),
        _sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "first."},
            },
        ),
        _sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _sse(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {}},
            },
        ),
        _sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": '{"path": "a.py"}'},
            },
        ),
        _sse("content_block_stop", {"type": "content_block_stop", "index": 1}),
        _sse(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"output_tokens": 42},
            },
        ),
        _sse("message_stop", {"type": "message_stop"}),
    ]
).encode()


def _chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "model": "gpt-5",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def openai_stream(with_usage: bool) -> bytes:
    parts = [
        _sse(None, _chunk({"role": "assistant", "content": ""})),
        _sse(None, _chunk({"content": "Hello"})),
        _sse(None, _chunk({"content": " world"})),
        _sse(
            None,
            _chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "run_tests", "arguments": ""},
                        }
                    ]
                }
            ),
        ),
        _sse(None, _chunk({"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]})),
        _sse(None, _chunk({}, "tool_calls")),
    ]
    if with_usage:
        parts.append(
            _sse(
                None,
                {
                    "id": "chatcmpl-1",
                    "object": "chat.completion.chunk",
                    "model": "gpt-5",
                    "choices": [],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 12, "total_tokens": 62},
                },
            )
        )
    parts.append("data: [DONE]\n\n")
    return "".join(parts).encode()


def anthropic_request(stream: bool = True, model: str = "claude-sonnet-5") -> bytes:
    return json.dumps(
        {
            "model": model,
            "max_tokens": 1024,
            "stream": stream,
            "messages": [
                {"role": "user", "content": "Fix the flaky test in test_writer.py"},
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "toolu_0", "name": "Bash", "input": {}}],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_0", "content": "ok"},
                        {"type": "text", "text": "now read a.py"},
                    ],
                },
            ],
        }
    ).encode()


def openai_request(stream: bool = True, include_usage: bool = False) -> bytes:
    payload: dict[str, Any] = {
        "model": "gpt-5",
        "stream": stream,
        "messages": [
            {"role": "system", "content": "You are Copilot."},
            {"role": "user", "content": "Run the tests please"},
        ],
    }
    if include_usage:
        payload["stream_options"] = {"include_usage": True}
    return json.dumps(payload).encode()


ANTHROPIC_MESSAGE = {
    "id": "msg_2",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-5",
    "content": [{"type": "text", "text": "Done."}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 300, "output_tokens": 20},
}


def anthropic_headers(key: str = API_KEY, **extra: str) -> dict[str, str]:
    return {
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "user-agent": "claude-cli/2.1.300 (external, cli)",
        **extra,
    }


def openai_headers(key: str = OPENAI_KEY, **extra: str) -> dict[str, str]:
    return {
        "content-type": "application/json",
        "authorization": f"Bearer {key}",
        "user-agent": "GitHubCopilotChat/0.40.0",
        **extra,
    }
