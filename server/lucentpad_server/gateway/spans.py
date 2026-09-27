"""Span building for gateway calls. Runs off the response path (in a worker thread)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from lucentpad_server.gateway.parse import (
    ResponseInfo,
    StreamParser,
    parse_json_response,
    parse_request,
)
from lucentpad_server.gateway.sessions import Session, new_hex_id
from lucentpad_server.schema import (
    PREVIEW_MAX_CHARS,
    Attr,
    Attributes,
    EventName,
    GatewayProvider,
    Span,
    SpanEvent,
    SpanStatus,
)

GATEWAY_SERVICE = "lucentpad-gateway"
_OP = {"anthropic": "messages", "openai": "chat.completions"}


@dataclass
class FailoverNote:
    time: datetime
    from_model: str
    to_model: str
    status_code: int
    retries: int


@dataclass
class CallRecord:
    """Everything the proxy learns about one model call; turned into a span at the end."""

    provider: GatewayProvider
    client: str
    session: Session
    fingerprint: str | None
    start: datetime
    t0: float
    request_body: bytes
    capture: bool
    ttfb_ms: float | None = None
    duration_ms: float = 0.0
    status_code: int | None = None
    streamed: bool = False
    stream: StreamParser | None = None
    response_body: bytes = b""
    content_encoding: str = ""
    completed: bool = False
    disconnected: bool = False
    stream_error: str | None = None
    transport_error: str | None = None
    failover: FailoverNote | None = None
    events: list[SpanEvent] = field(default_factory=list)


def _previews(inp: str | None, out: str | None, out_truncated: bool) -> Attributes:
    attrs: Attributes = {}
    if inp is not None:
        attrs[Attr.INPUT_PREVIEW] = inp[:PREVIEW_MAX_CHARS]
        if len(inp) > PREVIEW_MAX_CHARS:
            attrs[Attr.INPUT_TRUNCATED] = True
    if out is not None:
        attrs[Attr.OUTPUT_PREVIEW] = out
        if out_truncated:
            attrs[Attr.OUTPUT_TRUNCATED] = True
    return attrs


def root_span(
    session: Session, provider: GatewayProvider, fingerprint: str | None, start: datetime
) -> Span:
    """The session's root: named and grouped at once; the trace's end grows with its turns."""
    attrs: Attributes = {
        Attr.SERVICE_NAME: GATEWAY_SERVICE,
        Attr.CLIENT: session.client,
        Attr.SESSION_ID: session.session_id,
        Attr.GATEWAY_UPSTREAM: provider,
    }
    if fingerprint:
        attrs[Attr.KEY_FINGERPRINT] = fingerprint
    return Span(
        trace_id=session.trace_id,
        span_id=session.root_span_id,
        name=f"{session.client} session"[:200],
        kind="agent",
        source="gateway",
        start_time=start,
        end_time=start,
        attributes=attrs,
    )


def llm_span(call: CallRecord) -> Span:
    _, req = parse_request(call.provider, call.request_body, previews=call.capture)
    info: ResponseInfo
    if call.stream is not None:
        call.stream.close()
        info = call.stream.info
    else:
        info = parse_json_response(call.provider, call.response_body, call.content_encoding)

    request_model = req.model
    response_model = info.model or (call.failover.to_model if call.failover else request_model)
    attrs: Attributes = {
        Attr.SERVICE_NAME: GATEWAY_SERVICE,
        Attr.CLIENT: call.client,
        Attr.SESSION_ID: call.session.session_id,
        Attr.GEN_AI_SYSTEM: call.provider,
        Attr.GEN_AI_OPERATION: "chat",
        Attr.STREAMING: call.streamed,
        Attr.GATEWAY_UPSTREAM: call.provider,
    }
    if call.fingerprint:
        attrs[Attr.KEY_FINGERPRINT] = call.fingerprint
    if request_model:
        attrs[Attr.GEN_AI_REQUEST_MODEL] = request_model
    if response_model:
        attrs[Attr.GEN_AI_RESPONSE_MODEL] = response_model
    if info.input_tokens is not None:
        attrs[Attr.GEN_AI_INPUT_TOKENS] = info.input_tokens
    if info.output_tokens is not None:
        attrs[Attr.GEN_AI_OUTPUT_TOKENS] = info.output_tokens
    if info.cache_read_tokens is not None:
        attrs[Attr.GEN_AI_CACHE_READ_TOKENS] = info.cache_read_tokens
    if info.cache_creation_tokens is not None:
        attrs[Attr.GEN_AI_CACHE_CREATION_TOKENS] = info.cache_creation_tokens
    if info.finish_reasons:
        attrs[Attr.GEN_AI_FINISH_REASONS] = list(info.finish_reasons)
    if call.ttfb_ms is not None:
        attrs[Attr.TTFB_MS] = round(call.ttfb_ms, 3)
    if call.capture:
        out = None if info.output.empty else info.output.text()
        attrs.update(_previews(req.input_preview, out, info.output.truncated))

    status: SpanStatus = "ok"
    message: str | None = None
    if call.transport_error:
        status, message = "error", f"gateway: upstream unreachable ({call.transport_error})"
    elif call.disconnected and not call.completed:
        status, message = "error", "client disconnected"
    elif call.status_code is not None and call.status_code >= 400:
        status = "error"
        message = f"{call.provider}: upstream {call.status_code}"
        if info.error:
            message += f" {info.error}"
    elif info.error:
        status, message = "error", f"{call.provider}: {info.error}"
    elif call.stream_error:
        status, message = "error", f"upstream stream interrupted ({call.stream_error})"

    events = list(call.events)
    if call.failover is not None:
        f = call.failover
        events.append(
            SpanEvent(
                name=EventName.FAILOVER,
                time=f.time,
                attributes={
                    Attr.FAILOVER_FROM_MODEL: f.from_model,
                    Attr.FAILOVER_TO_MODEL: f.to_model,
                    Attr.FAILOVER_STATUS_CODE: f.status_code,
                    Attr.FAILOVER_RETRIES: f.retries,
                },
            )
        )
    name_model = response_model or request_model or "unknown"
    return Span(
        trace_id=call.session.trace_id,
        span_id=new_hex_id(8),
        parent_span_id=call.session.root_span_id,
        name=f"{_OP[call.provider]} {name_model}"[:200],
        kind="llm",
        source="gateway",
        start_time=call.start,
        end_time=call.start + timedelta(milliseconds=max(call.duration_ms, 0.0)),
        status=status,
        status_message=message[:2000] if message else None,
        attributes=attrs,
        events=events[:100],
    )


def utcnow() -> datetime:
    return datetime.now(UTC)
