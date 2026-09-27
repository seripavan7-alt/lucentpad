"""Gateway proxy (M2 step 2) against fake upstreams: byte-identical streams, timing, headers,
key hygiene, allowlist, failover, sessions, disconnects, recording. No network."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from lucentpad_server.gateway.config import GatewayConfig
from lucentpad_server.schema import Attr, EventName

from .gateway_fakes import (
    ANTHROPIC_MESSAGE,
    ANTHROPIC_STREAM,
    API_KEY,
    OPENAI_KEY,
    FakeIngest,
    FakeStream,
    FakeUpstream,
    anthropic_headers,
    anthropic_request,
    asgi_call,
    asgi_client,
    chop,
    json_reply,
    make_gateway,
    openai_headers,
    openai_request,
    openai_stream,
    sse_reply,
)

MESSAGES = "/gateway/anthropic/v1/messages"
CHAT = "/gateway/openai/v1/chat/completions"


# --------------------------------------------------------------------------- streaming


async def test_anthropic_stream_is_byte_identical_and_recorded() -> None:
    stream = FakeStream(chop(ANTHROPIC_STREAM))
    upstream = FakeUpstream([sse_reply(stream)])
    app, proxy, ingest = make_gateway(upstream)
    res = await asgi_call(
        app, "POST", MESSAGES + "?beta=true", headers=anthropic_headers(), body=anthropic_request()
    )
    await proxy.drain()

    assert res.status == 200
    assert res.body == ANTHROPIC_STREAM
    assert [c for _, c in res.chunks] == stream.chunks  # same chunk boundaries, nothing merged
    assert dict(res.headers)[b"content-type"] == b"text/event-stream"
    assert dict(res.headers)[b"request-id"] == b"req_1"
    assert str(upstream.requests[0].url) == "https://anthropic.test/v1/messages?beta=true"
    assert stream.closed

    (root,) = ingest.roots()
    (span,) = ingest.llm()
    assert root.name == "claude-code session"
    assert root.parent_span_id is None and root.end_time == root.start_time
    assert root.attributes[Attr.SERVICE_NAME] == "lucentpad-gateway"
    assert span.trace_id == root.trace_id and span.parent_span_id == root.span_id
    assert span.name == "messages claude-sonnet-5"
    assert span.status == "ok" and span.source == "gateway"
    a = span.attributes
    assert a[Attr.GEN_AI_SYSTEM] == "anthropic"
    assert a[Attr.GEN_AI_REQUEST_MODEL] == a[Attr.GEN_AI_RESPONSE_MODEL] == "claude-sonnet-5"
    assert a[Attr.GEN_AI_INPUT_TOKENS] == 1200
    assert a[Attr.GEN_AI_OUTPUT_TOKENS] == 42
    assert a[Attr.GEN_AI_FINISH_REASONS] == ["tool_use"]
    assert a[Attr.STREAMING] is True
    assert a[Attr.GATEWAY_UPSTREAM] == "anthropic"
    assert a[Attr.CLIENT] == "claude-code"
    assert a[Attr.INPUT_PREVIEW] == "[tool_result Bash]\nnow read a.py"
    assert a[Attr.OUTPUT_PREVIEW] == "Let me read the file — ünïcode first.\n[tool_use Read]"
    assert isinstance(a[Attr.TTFB_MS], float)
    assert Attr.COST_USD not in a  # the server prices tokens on insert


@pytest.mark.parametrize("with_usage", [True, False])
async def test_openai_stream_is_byte_identical(with_usage: bool) -> None:
    raw = openai_stream(with_usage)
    upstream = FakeUpstream([sse_reply(FakeStream(chop(raw, (33, 5, 200))))])
    app, proxy, ingest = make_gateway(upstream)
    body = openai_request(include_usage=with_usage)
    res = await asgi_call(app, "POST", CHAT, headers=openai_headers(), body=body)
    await proxy.drain()

    assert res.status == 200 and res.body == raw
    assert upstream.bodies[0] == body  # never injects stream_options
    (span,) = ingest.llm()
    a = span.attributes
    assert span.name == "chat.completions gpt-5"
    assert a[Attr.CLIENT] == "copilot-chat"
    assert a[Attr.GEN_AI_FINISH_REASONS] == ["tool_calls"]
    assert a[Attr.OUTPUT_PREVIEW] == "Hello world\n[tool_use run_tests]"
    assert a[Attr.INPUT_PREVIEW] == "Run the tests please"
    if with_usage:
        assert (a[Attr.GEN_AI_INPUT_TOKENS], a[Attr.GEN_AI_OUTPUT_TOKENS]) == (50, 12)
    else:
        assert Attr.GEN_AI_INPUT_TOKENS not in a and Attr.GEN_AI_OUTPUT_TOKENS not in a


async def test_first_chunk_reaches_client_before_upstream_finishes() -> None:
    parts = chop(ANTHROPIC_STREAM, (400,))
    stream = FakeStream(parts, delay=0.15)
    upstream = FakeUpstream([sse_reply(stream)])
    app, _proxy, _ = make_gateway(upstream)
    res = await asgi_call(
        app, "POST", MESSAGES, headers=anthropic_headers(), body=anthropic_request()
    )
    assert stream.finished_at is not None
    first_t = res.chunks[0][0]
    assert first_t - res.started < 0.1
    assert stream.finished_at - first_t >= 0.15 * (len(parts) - 1) * 0.9
    # each chunk arrived right after the upstream produced it, not all at the end
    gaps = [b[0] - a[0] for a, b in zip(res.chunks, res.chunks[1:], strict=False)]
    assert all(g >= 0.1 for g in gaps)
    assert res.body == ANTHROPIC_STREAM


async def test_client_disconnect_closes_upstream_and_records_error() -> None:
    events = [e + b"\n\n" for e in ANTHROPIC_STREAM.split(b"\n\n") if e]
    stream = FakeStream(events, delay=0.05)
    upstream = FakeUpstream([sse_reply(stream)])
    app, proxy, ingest = make_gateway(upstream)
    res = await asgi_call(
        app,
        "POST",
        MESSAGES,
        headers=anthropic_headers(),
        body=anthropic_request(),
        disconnect_after_chunks=2,
    )
    await proxy.drain()
    assert len(res.chunks) < len(stream.chunks)
    assert stream.closed
    assert stream.sent < len(stream.chunks)
    (span,) = ingest.llm()
    assert span.status == "error" and span.status_message == "client disconnected"
    assert span.attributes[Attr.GEN_AI_INPUT_TOKENS] == 1200  # what arrived was still parsed


async def test_stream_error_event_marks_span() -> None:
    raw = (
        b'event: error\ndata: {"type":"error","error":{"type":"overloaded_error",'
        b'"message":"Overloaded"}}\n\n'
    )
    upstream = FakeUpstream([sse_reply(FakeStream([raw]))])
    app, proxy, ingest = make_gateway(upstream)
    res = await asgi_call(
        app, "POST", MESSAGES, headers=anthropic_headers(), body=anthropic_request()
    )
    await proxy.drain()
    assert res.body == raw
    (span,) = ingest.llm()
    assert span.status == "error"
    assert span.status_message == "anthropic: overloaded_error: Overloaded"


# --------------------------------------------------------------------------- non-streamed


async def test_non_streamed_pass_through() -> None:
    upstream = FakeUpstream([json_reply(ANTHROPIC_MESSAGE, 200, **{"x-should-retry": "false"})])
    app, proxy, ingest = make_gateway(upstream)
    async with asgi_client(app) as c:
        r = await c.post(
            MESSAGES, content=anthropic_request(stream=False), headers=anthropic_headers()
        )
    await proxy.drain()
    assert r.status_code == 200
    assert r.content == json.dumps(ANTHROPIC_MESSAGE).encode()
    assert r.headers["x-should-retry"] == "false"
    assert r.headers["content-length"] == str(len(r.content))
    (span,) = ingest.llm()
    a = span.attributes
    assert a[Attr.STREAMING] is False
    assert (a[Attr.GEN_AI_INPUT_TOKENS], a[Attr.GEN_AI_OUTPUT_TOKENS]) == (300, 20)
    assert a[Attr.OUTPUT_PREVIEW] == "Done."
    assert a[Attr.GEN_AI_FINISH_REASONS] == ["end_turn"]


async def test_openai_non_streamed_usage() -> None:
    completion = {
        "id": "c1",
        "object": "chat.completion",
        "model": "gpt-5",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 9, "completion_tokens": 3},
    }
    upstream = FakeUpstream([json_reply(completion)])
    app, proxy, ingest = make_gateway(upstream)
    async with asgi_client(app) as c:
        r = await c.post(CHAT, content=openai_request(stream=False), headers=openai_headers())
    await proxy.drain()
    assert r.json() == completion
    (span,) = ingest.llm()
    assert span.attributes[Attr.GEN_AI_INPUT_TOKENS] == 9
    assert span.attributes[Attr.GEN_AI_OUTPUT_TOKENS] == 3


async def test_upstream_errors_pass_through_verbatim() -> None:
    err = {"type": "error", "error": {"type": "invalid_request_error", "message": "bad max_tokens"}}
    upstream = FakeUpstream([json_reply(err, status=400)])
    app, proxy, ingest = make_gateway(upstream)
    async with asgi_client(app) as c:
        r = await c.post(
            MESSAGES, content=anthropic_request(stream=False), headers=anthropic_headers()
        )
    await proxy.drain()
    assert r.status_code == 400 and r.json() == err
    assert len(upstream.requests) == 1  # 4xx other than 429: no retry
    (span,) = ingest.llm()
    assert span.status == "error"
    assert span.status_message == "anthropic: upstream 400 invalid_request_error: bad max_tokens"


async def test_unreachable_upstream_is_502_in_provider_shape() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    for path, headers, body, shape in (
        (MESSAGES, anthropic_headers(), anthropic_request(), "anthropic"),
        (CHAT, openai_headers(), openai_request(), "openai"),
    ):
        app, proxy, ingest = make_gateway(FakeUpstream([boom]))
        async with asgi_client(app) as c:
            r = await c.post(path, content=body, headers=headers)
        await proxy.drain()
        assert r.status_code == 502
        data = r.json()
        if shape == "anthropic":
            assert data["type"] == "error" and data["error"]["type"] == "api_error"
        else:
            assert data["error"]["type"] == "api_error"
        (span,) = ingest.llm()
        assert span.status == "error"
        assert span.status_message == "gateway: upstream unreachable (ConnectError)"


# --------------------------------------------------------------------------- headers + keys


async def test_headers_forwarded_and_hop_by_hop_dropped() -> None:
    upstream = FakeUpstream([json_reply(ANTHROPIC_MESSAGE)])
    app, _proxy, _ = make_gateway(upstream)
    headers = anthropic_headers(
        **{
            "anthropic-beta": "interleaved-thinking-2025-05-14,context-management-2025-06-27",
            "x-claude-code-session-id": "sess-1",
            "connection": "keep-alive, x-drop-me",
            "x-drop-me": "1",
            "keep-alive": "timeout=5",
            "accept-encoding": "gzip, br",
        }
    )
    await asgi_call(app, "POST", MESSAGES, headers=headers, body=anthropic_request(stream=False))
    sent = upstream.requests[0].headers
    assert sent["anthropic-version"] == "2023-06-01"
    assert sent["anthropic-beta"] == headers["anthropic-beta"]
    assert sent["x-api-key"] == API_KEY  # forwarded untouched
    assert sent["x-claude-code-session-id"] == "sess-1"
    assert sent["user-agent"] == headers["user-agent"]
    assert sent["host"] == "anthropic.test"
    assert sent["accept-encoding"] == "identity"
    assert sent["content-length"] == str(len(anthropic_request(stream=False)))
    for dropped in ("keep-alive", "x-drop-me"):
        assert dropped not in sent
    assert sent["connection"] == "keep-alive"  # the gateway's own hop, not the client's value
    assert "accept" not in sent  # no httpx defaults the client didn't send


async def test_bearer_forwarded_for_openai() -> None:
    upstream = FakeUpstream([sse_reply(FakeStream([openai_stream(False)]))])
    app, _proxy, _ = make_gateway(upstream)
    await asgi_call(app, "POST", CHAT, headers=openai_headers(), body=openai_request())
    assert upstream.requests[0].headers["authorization"] == f"Bearer {OPENAI_KEY}"


async def test_keys_never_in_spans_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    cookie = "session=COOKIE-SECRET-42"

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    upstream = FakeUpstream(
        [
            sse_reply(FakeStream(chop(ANTHROPIC_STREAM))),
            json_reply({"type": "error", "error": {"type": "rate_limit_error"}}, status=429),
            json_reply({"type": "error", "error": {"type": "rate_limit_error"}}, status=429),
            json_reply(ANTHROPIC_MESSAGE),
            boom,
        ]
    )
    app, proxy, ingest = make_gateway(upstream)
    h = anthropic_headers(cookie=cookie)
    await asgi_call(app, "POST", MESSAGES, headers=h, body=anthropic_request())
    await asgi_call(app, "POST", MESSAGES, headers=h, body=anthropic_request(stream=False))
    await asgi_call(app, "POST", MESSAGES, headers=h, body=anthropic_request(stream=False))
    await asgi_call(
        app,
        "POST",
        CHAT,
        headers=openai_headers(cookie=cookie),
        body=openai_request(stream=False),
    )
    await proxy.drain()
    assert len(ingest.llm()) == 4
    dumped = json.dumps([s.model_dump(mode="json") for s in ingest.spans])
    for secret in (API_KEY, OPENAI_KEY, "SECRET", "COOKIE-SECRET-42"):
        assert secret not in dumped
        assert secret not in caplog.text
    fingerprints = {s.attributes[Attr.KEY_FINGERPRINT] for s in ingest.spans}
    assert len(fingerprints) == 2
    assert all(isinstance(f, str) and len(f) == 12 for f in fingerprints)


# --------------------------------------------------------------------------- allowlist


@pytest.mark.parametrize(
    ("method", "path", "shape"),
    [
        ("POST", "/gateway/anthropic/v1/complete", "anthropic"),
        ("GET", "/gateway/anthropic/v1/messages", "anthropic"),
        ("POST", "/gateway/anthropic/v1/files", "anthropic"),
        ("POST", "/gateway/openai/v1/embeddings", "openai"),
        ("POST", "/gateway/openai/v1/responses", "openai"),
        ("GET", "/gateway/openai/v1/models/a/b", "openai"),
    ],
)
async def test_non_allowlisted_paths_are_404(method: str, path: str, shape: str) -> None:
    upstream = FakeUpstream([json_reply({})])
    app, _proxy, ingest = make_gateway(upstream)
    async with asgi_client(app) as c:
        r = await c.request(method, path, headers=anthropic_headers())
    assert r.status_code == 404
    data = r.json()
    if shape == "anthropic":
        assert data["type"] == "error" and data["error"]["type"] == "not_found_error"
    else:
        assert data["error"]["type"] == "invalid_request_error"
    assert upstream.requests == [] and ingest.spans == []


@pytest.mark.parametrize(
    ("method", "path", "upstream_url"),
    [
        ("GET", "/gateway/anthropic/v1/models", "https://anthropic.test/v1/models"),
        (
            "GET",
            "/gateway/anthropic/v1/models/claude-sonnet-5",
            "https://anthropic.test/v1/models/claude-sonnet-5",
        ),
        (
            "POST",
            "/gateway/anthropic/v1/messages/count_tokens",
            "https://anthropic.test/v1/messages/count_tokens",
        ),
        ("GET", "/gateway/openai/v1/models?limit=5", "https://openai.test/v1/models?limit=5"),
    ],
)
async def test_side_endpoints_forwarded_without_spans(
    method: str, path: str, upstream_url: str
) -> None:
    upstream = FakeUpstream([json_reply({"data": []})])
    app, proxy, ingest = make_gateway(upstream)
    async with asgi_client(app) as c:
        r = await c.request(method, path, headers=anthropic_headers(), content=b"{}")
    await proxy.drain()
    assert r.status_code == 200 and r.json() == {"data": []}
    assert str(upstream.requests[0].url) == upstream_url
    assert ingest.spans == []  # not model calls: no turn, no session


# --------------------------------------------------------------------------- failover (D18)

RATE_LIMIT = {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}


async def test_failover_on_429_retries_then_falls_back() -> None:
    fallback = {**ANTHROPIC_MESSAGE, "model": "claude-haiku-4-5"}
    upstream = FakeUpstream(
        [json_reply(RATE_LIMIT, 429), json_reply(RATE_LIMIT, 429), json_reply(fallback)]
    )
    app, proxy, ingest = make_gateway(upstream)
    async with asgi_client(app) as c:
        r = await c.post(
            MESSAGES, content=anthropic_request(stream=False), headers=anthropic_headers()
        )
    await proxy.drain()
    assert r.status_code == 200 and r.json() == fallback
    models = [json.loads(b)["model"] for b in upstream.bodies]
    assert models == ["claude-sonnet-5", "claude-sonnet-5", "claude-haiku-4-5"]
    assert upstream.requests[2].headers["x-api-key"] == API_KEY
    (span,) = ingest.llm()
    assert span.status == "ok"
    assert span.attributes[Attr.GEN_AI_REQUEST_MODEL] == "claude-sonnet-5"
    assert span.attributes[Attr.GEN_AI_RESPONSE_MODEL] == "claude-haiku-4-5"
    (event,) = span.events
    assert event.name == EventName.FAILOVER
    assert event.attributes == {
        Attr.FAILOVER_FROM_MODEL: "claude-sonnet-5",
        Attr.FAILOVER_TO_MODEL: "claude-haiku-4-5",
        Attr.FAILOVER_STATUS_CODE: 429,
        Attr.FAILOVER_RETRIES: 1,
    }


async def test_503_then_ok_on_retry_has_no_failover() -> None:
    completion: dict[str, object] = {"id": "c", "model": "gpt-5", "choices": [], "usage": None}
    upstream = FakeUpstream([json_reply({"error": {"message": "x"}}, 503), json_reply(completion)])
    app, proxy, ingest = make_gateway(upstream)
    async with asgi_client(app) as c:
        r = await c.post(CHAT, content=openai_request(stream=False), headers=openai_headers())
    await proxy.drain()
    assert r.status_code == 200
    assert len(upstream.requests) == 2
    assert ingest.llm()[0].events == []


async def test_failover_gpt5_to_mini_and_final_error_passes_through() -> None:
    err = {"error": {"message": "overloaded", "type": "server_error"}}
    upstream = FakeUpstream([json_reply(err, 503)])
    app, proxy, ingest = make_gateway(upstream)
    async with asgi_client(app) as c:
        r = await c.post(CHAT, content=openai_request(stream=False), headers=openai_headers())
    await proxy.drain()
    assert r.status_code == 503 and r.json() == err
    assert [json.loads(b)["model"] for b in upstream.bodies] == ["gpt-5", "gpt-5", "gpt-5-mini"]
    (span,) = ingest.llm()
    assert (
        span.status == "error" and span.events[0].attributes[Attr.FAILOVER_TO_MODEL] == "gpt-5-mini"
    )


async def test_no_failover_for_streamed_requests() -> None:
    upstream = FakeUpstream([json_reply(RATE_LIMIT, 429)])
    app, proxy, ingest = make_gateway(upstream)
    async with asgi_client(app) as c:
        r = await c.post(
            MESSAGES, content=anthropic_request(stream=True), headers=anthropic_headers()
        )
    await proxy.drain()
    assert r.status_code == 429 and r.json() == RATE_LIMIT
    assert len(upstream.requests) == 1
    assert ingest.llm()[0].events == []


async def test_no_failover_mid_stream_error_status() -> None:
    upstream = FakeUpstream([sse_reply(FakeStream([b"event: ping\ndata: {}\n\n"]), status=503)])
    app, proxy, _ = make_gateway(upstream)
    res = await asgi_call(
        app, "POST", MESSAGES, headers=anthropic_headers(), body=anthropic_request(False)
    )
    await proxy.drain()
    assert res.status == 503 and len(upstream.requests) == 1


async def test_failover_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUCENTPAD_GATEWAY_FAILOVER", "0")
    assert GatewayConfig.from_env().failover is False
    upstream = FakeUpstream([json_reply(RATE_LIMIT, 429)])
    app, _proxy, _ = make_gateway(upstream, failover=False)
    async with asgi_client(app) as c:
        r = await c.post(
            MESSAGES, content=anthropic_request(stream=False), headers=anthropic_headers()
        )
    assert r.status_code == 429 and len(upstream.requests) == 1


# --------------------------------------------------------------------------- sessions


async def test_session_grouping_by_key_and_gap() -> None:
    now = [1000.0]
    upstream = FakeUpstream([json_reply(ANTHROPIC_MESSAGE)])
    app, proxy, ingest = make_gateway(upstream)
    proxy.sessions._clock = lambda: now[0]  # fake clock for the idle gap

    async def call(key: str = API_KEY) -> str:
        await asgi_call(
            app, "POST", MESSAGES, headers=anthropic_headers(key), body=anthropic_request(False)
        )
        await proxy.drain()
        return ingest.llm()[-1].trace_id

    t1 = await call()
    now[0] += 60
    t2 = await call()
    assert t1 == t2 and len(ingest.roots()) == 1
    now[0] += 31 * 60
    t3 = await call()
    assert t3 != t1 and len(ingest.roots()) == 2
    t4 = await call("sk-ant-other-key")
    assert t4 != t3 and len(ingest.roots()) == 3
    sessions = {s.attributes[Attr.SESSION_ID] for s in ingest.roots()}
    assert len(sessions) == 3


async def test_session_header_wins() -> None:
    now = [0.0]
    upstream = FakeUpstream([json_reply(ANTHROPIC_MESSAGE)])
    app, proxy, ingest = make_gateway(upstream)
    proxy.sessions._clock = lambda: now[0]

    for key in (API_KEY, "sk-ant-another"):
        await asgi_call(
            app,
            "POST",
            MESSAGES,
            headers=anthropic_headers(key, **{"x-claude-code-session-id": "abc-123"}),
            body=anthropic_request(False),
        )
        now[0] += 3 * 3600  # even past the idle gap
    await asgi_call(
        app,
        "POST",
        MESSAGES,
        headers=anthropic_headers(API_KEY, **{"x-claude-code-session-id": "other"}),
        body=anthropic_request(False),
    )
    await proxy.drain()
    traces = [s.trace_id for s in ingest.llm()]
    assert traces[0] == traces[1] != traces[2]
    first_root = ingest.roots()[0]
    assert first_root.attributes[Attr.SESSION_ID] == "abc-123"
    # Deterministic ids: a restarted gateway continues the same trace (re-sent root is a no-op).
    _app2, proxy2, _ = make_gateway(upstream)
    session, _new = proxy2.sessions.lookup("claude-code", "abc-123", None)
    assert session.trace_id == traces[0]
    assert session.root_span_id == first_root.span_id


async def test_concurrent_requests_same_session() -> None:
    def fresh(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=FakeStream(chop(ANTHROPIC_STREAM), delay=0.01),
        )

    upstream = FakeUpstream([fresh])
    app, proxy, ingest = make_gateway(upstream)
    await asyncio.gather(
        *(
            asgi_call(app, "POST", MESSAGES, headers=anthropic_headers(), body=anthropic_request())
            for _ in range(5)
        )
    )
    await proxy.drain()
    assert len(ingest.roots()) == 1
    assert len({s.trace_id for s in ingest.llm()}) == 1 and len(ingest.llm()) == 5


async def test_ingest_full_counts_dropped_spans() -> None:
    upstream = FakeUpstream([json_reply(ANTHROPIC_MESSAGE)])
    app, proxy, _ = make_gateway(upstream, ingest=FakeIngest(accept=False))
    async with asgi_client(app) as c:
        r = await c.post(
            MESSAGES, content=anthropic_request(stream=False), headers=anthropic_headers()
        )
    await proxy.drain()
    assert r.status_code == 200
    assert proxy.dropped_spans == 2  # root + llm


async def test_capture_content_off() -> None:
    upstream = FakeUpstream([json_reply(ANTHROPIC_MESSAGE)])
    app, proxy, ingest = make_gateway(upstream, capture_content=False)
    async with asgi_client(app) as c:
        await c.post(MESSAGES, content=anthropic_request(stream=False), headers=anthropic_headers())
    await proxy.drain()
    a = ingest.llm()[0].attributes
    assert Attr.INPUT_PREVIEW not in a and Attr.OUTPUT_PREVIEW not in a
    assert a[Attr.GEN_AI_INPUT_TOKENS] == 300
