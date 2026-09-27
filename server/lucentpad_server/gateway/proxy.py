"""``HttpGatewayProxy``: the app's ``GatewayProxy``.

Request path (the hot path) does only cheap work: allowlist check, header filtering, a
fingerprint hash, client detection and a session lookup, then forwards. Streams are relayed
chunk by chunk, unmodified, while a side parser reads usage from them. Spans are built after
the response has gone out (in a worker thread) and offered to the ingest queue without waiting.

Headers: everything is forwarded except hop-by-hop headers (and any the ``Connection`` header
names), ``host``, ``content-length`` and ``accept-encoding``. ``accept-encoding: identity`` is
sent instead, so the upstream returns uncompressed bytes that the side parser can read and the
client still gets exactly the upstream's bytes. (If an upstream compresses anyway, its bytes
and ``content-encoding`` are relayed untouched; gzip/deflate are inflated on the side copy only.)
Credentials (``x-api-key``, ``authorization``, cookies) are forwarded but never logged, stored,
or put on spans; only ``Attr.KEY_FINGERPRINT`` (salted SHA-256, 12 hex) is kept.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from functools import partial
from typing import Any

import anyio
import httpx
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.types import Receive, Scope, Send

from lucentpad_server.gateway.clients import detect_client, key_fingerprint
from lucentpad_server.gateway.config import GatewayConfig
from lucentpad_server.gateway.parse import StreamParser
from lucentpad_server.gateway.sessions import SessionTracker
from lucentpad_server.gateway.spans import (
    CallRecord,
    FailoverNote,
    llm_span,
    root_span,
    utcnow,
)
from lucentpad_server.ingest import IngestPipeline
from lucentpad_server.schema import GatewayProvider, Span

log = logging.getLogger(__name__)

HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)
_DROP_REQUEST = HOP_BY_HOP | {"host", "content-length", "accept-encoding", "expect"}
_DROP_RESPONSE = HOP_BY_HOP | {"content-length"}

_CHAT = "chat"
_PASS = "pass"  # noqa: S105 - a route kind, not a secret
_MODEL_ID = re.compile(r"v1/models/[^/]+")
_ROUTES: dict[str, dict[tuple[str, str], str]] = {
    "anthropic": {
        ("POST", "v1/messages"): _CHAT,
        ("POST", "v1/messages/count_tokens"): _PASS,
        ("GET", "v1/models"): _PASS,
    },
    "openai": {
        ("POST", "v1/chat/completions"): _CHAT,
        ("GET", "v1/models"): _PASS,
    },
}
_RETRY_STATUSES = frozenset({429}) | frozenset(range(500, 600))


def _route(provider: str, method: str, path: str) -> str | None:
    kind = _ROUTES.get(provider, {}).get((method, path))
    if kind is None and method == "GET" and _MODEL_ID.fullmatch(path):
        return _PASS
    return kind


def error_body(provider: str, kind: str, message: str) -> bytes:
    """A JSON error in the provider's own shape, so clients show it sensibly."""
    if provider == "anthropic":
        payload: dict[str, Any] = {"type": "error", "error": {"type": kind, "message": message}}
    else:
        payload = {"error": {"message": message, "type": kind, "param": None, "code": None}}
    return json.dumps(payload).encode()


def _error_response(provider: str, status: int, kind: str, message: str) -> Response:
    return Response(
        error_body(provider, kind, message), status_code=status, media_type="application/json"
    )


def _request_headers(request: Request) -> list[tuple[bytes, bytes]]:
    named = {
        t.strip().lower() for t in request.headers.get("connection", "").split(",") if t.strip()
    }
    out = [
        (k, v)
        for k, v in request.headers.raw
        if k.decode("latin-1").lower() not in _DROP_REQUEST
        and k.decode("latin-1").lower() not in named
    ]
    out.append((b"accept-encoding", b"identity"))
    return out


def _response_headers(resp: httpx.Response) -> list[tuple[bytes, bytes]]:
    named = {t.strip().lower() for t in resp.headers.get("connection", "").split(",")}
    return [
        (k.lower(), v)
        for k, v in resp.headers.raw
        if k.decode("latin-1").lower() not in _DROP_RESPONSE
        and k.decode("latin-1").lower() not in named
    ]


def _build(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: list[tuple[bytes, bytes]],
    body: bytes,
) -> httpx.Request:
    """The upstream request. httpx's default ``accept`` / ``user-agent`` are removed unless the
    client sent them (its own ``connection: keep-alive`` stays: that hop is ours)."""
    request = client.build_request(method, url, headers=headers, content=body)
    sent = {k.lower() for k, _ in headers}
    for default in (b"accept", b"user-agent"):
        if default not in sent and default.decode() in request.headers:
            del request.headers[default.decode()]
    return request


def _is_stream(resp: httpx.Response) -> bool:
    content_type: str = resp.headers.get("content-type", "")
    return content_type.lower().startswith("text/event-stream")


class _RelayResponse(StreamingResponse):
    """A ``StreamingResponse`` that always watches for client disconnect (whatever the ASGI
    spec version), always closes its body iterator, and calls ``on_done`` once at the end."""

    def __init__(
        self,
        content: AsyncIterator[bytes],
        status_code: int,
        raw_headers: list[tuple[bytes, bytes]],
        close_upstream: Callable[[], Awaitable[None]],
        on_done: Callable[[], None] | None,
    ) -> None:
        super().__init__(content, status_code=status_code)
        self.raw_headers = raw_headers
        self._iter = content
        self._close_upstream = close_upstream
        self._on_done = on_done

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            async with anyio.create_task_group() as tg:

                async def stream() -> None:
                    try:
                        await self.stream_response(send)
                    except OSError:
                        pass  # the client went away while we were sending
                    finally:
                        tg.cancel_scope.cancel()

                tg.start_soon(stream)
                await self.listen_for_disconnect(receive)
                tg.cancel_scope.cancel()
        finally:
            with anyio.CancelScope(shield=True):
                aclose = getattr(self._iter, "aclose", None)
                if aclose is not None:
                    await aclose()
                # An iterator that never started skips its own cleanup: close upstream here too.
                await self._close_upstream()
                if self._on_done is not None:
                    self._on_done()


class HttpGatewayProxy:
    """Forwards to the real provider APIs with one long-lived httpx client per upstream."""

    def __init__(
        self,
        config: GatewayConfig,
        ingest: IngestPipeline | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._ingest = ingest
        timeout = httpx.Timeout(
            connect=config.connect_timeout_s,
            read=config.read_timeout_s,
            write=60.0,
            pool=config.connect_timeout_s,
        )
        self._clients: dict[str, httpx.AsyncClient] = {}
        for provider in ("anthropic", "openai"):
            client = httpx.AsyncClient(
                transport=transport,
                timeout=timeout,
                follow_redirects=False,
                limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
            )
            self._clients[provider] = client
        self.sessions = SessionTracker(config.session_gap_s, clock=clock)
        self._tasks: set[asyncio.Task[None]] = set()
        self.dropped_spans = 0
        """Spans the ingest queue refused (full) or that failed to build."""

    # ------------------------------------------------------------------ lifecycle

    async def drain(self) -> None:
        """Wait for pending span recording (tests, shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def aclose(self, drain_timeout_s: float = 2.0) -> None:
        try:
            await asyncio.wait_for(self.drain(), drain_timeout_s)
        except TimeoutError:
            log.warning("gateway: %d span recordings still pending at shutdown", len(self._tasks))
        for client in self._clients.values():
            await client.aclose()

    # ------------------------------------------------------------------ recording

    def _offer(self, spans: list[Span]) -> None:
        if self._ingest is None:
            return
        if not self._ingest.offer(spans):
            self.dropped_spans += len(spans)
            log.warning("gateway: ingest queue full, dropped %d span(s)", len(spans))

    def _finish(self, call: CallRecord) -> None:
        """Schedule span building off the response path."""
        if call.duration_ms == 0.0:
            call.duration_ms = (time.perf_counter() - call.t0) * 1000
        task = asyncio.get_running_loop().create_task(self._record(call))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _record(self, call: CallRecord) -> None:
        try:
            span = await asyncio.to_thread(llm_span, call)
        except Exception:
            self.dropped_spans += 1
            log.exception("gateway: could not build the span for a %s call", call.provider)
            return
        self._offer([span])

    # ------------------------------------------------------------------ forwarding

    async def forward(self, provider: GatewayProvider, path: str, request: Request) -> Response:
        t0 = time.perf_counter()
        start = utcnow()
        path = path.lstrip("/")
        kind = _route(provider, request.method, path)
        if kind is None:
            return _error_response(
                provider,
                404,
                "not_found_error" if provider == "anthropic" else "invalid_request_error",
                f"lucentpad gateway: {request.method} /{path} is not proxied",
            )
        body = await request.body()
        url = f"{self.config.upstream(provider).rstrip('/')}/{path}"
        if request.url.query:
            url += f"?{request.url.query}"
        headers = _request_headers(request)
        client = self._clients[provider]

        call: CallRecord | None = None
        if kind == _CHAT:
            fingerprint = key_fingerprint(request.headers, self.config.key_salt)
            who = detect_client(request.headers, self.config.extra_client_rules)
            header_session = next(
                (
                    v
                    for h in self.config.session_headers
                    if (v := request.headers.get(h, "").strip())
                ),
                None,
            )
            session, new = self.sessions.lookup(who, header_session, fingerprint)
            if new:
                self._offer([root_span(session, provider, fingerprint, start)])
            call = CallRecord(
                provider=provider,
                client=who,
                session=session,
                fingerprint=fingerprint,
                start=start,
                t0=t0,
                request_body=body,
                capture=self.config.capture_content,
            )

        try:
            resp = await client.send(
                _build(client, request.method, url, headers, body),
                stream=True,
            )
        except httpx.HTTPError as exc:
            return self._unreachable(provider, call, exc)

        if _is_stream(resp):
            return self._relay(provider, resp, call)

        try:
            raw, ttfb = await self._read(resp, t0)
            if call is not None and self._can_fail_over(resp.status_code):
                resp, raw, ttfb = await self._fail_over(
                    provider, client, request.method, url, headers, body, resp, raw, ttfb, call
                )
        except httpx.HTTPError as exc:
            return self._unreachable(provider, call, exc)

        out = Response(content=raw, status_code=resp.status_code)
        out.raw_headers = [*_response_headers(resp), (b"content-length", str(len(raw)).encode())]
        if call is not None:
            call.status_code = resp.status_code
            call.ttfb_ms = ttfb
            call.response_body = raw
            call.content_encoding = resp.headers.get("content-encoding", "")
            call.completed = True
            call.duration_ms = (time.perf_counter() - t0) * 1000
            self._finish(call)
        return out

    def _unreachable(
        self, provider: GatewayProvider, call: CallRecord | None, exc: Exception
    ) -> Response:
        name = type(exc).__name__
        log.warning("gateway: %s upstream unreachable (%s)", provider, name)
        if call is not None:
            call.transport_error = name
            self._finish(call)
        return _error_response(
            provider, 502, "api_error", f"lucentpad gateway: upstream unreachable ({name})"
        )

    @staticmethod
    async def _read(resp: httpx.Response, t0: float) -> tuple[bytes, float | None]:
        parts: list[bytes] = []
        ttfb: float | None = None
        try:
            async for chunk in resp.aiter_raw():
                if ttfb is None:
                    ttfb = (time.perf_counter() - t0) * 1000
                parts.append(chunk)
        finally:
            await resp.aclose()
        return b"".join(parts), ttfb

    # ------------------------------------------------------------------ failover (D18)

    def _can_fail_over(self, status: int) -> bool:
        return self.config.failover and status in _RETRY_STATUSES

    async def _fail_over(
        self,
        provider: GatewayProvider,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        headers: list[tuple[bytes, bytes]],
        body: bytes,
        resp: httpx.Response,
        raw: bytes,
        ttfb: float | None,
        call: CallRecord,
    ) -> tuple[httpx.Response, bytes, float | None]:
        """Non-streamed only: retry once on the same model, then once on the fallback model."""
        try:
            data = json.loads(body)
        except ValueError:
            return resp, raw, ttfb
        if not isinstance(data, dict) or data.get("stream") is True:
            return resp, raw, ttfb

        async def attempt(content: bytes) -> tuple[httpx.Response, bytes, float | None]:
            await asyncio.sleep(self.config.failover_backoff_s)
            r = await client.send(_build(client, method, url, headers, content), stream=True)
            if _is_stream(r):  # not expected for a non-streamed request; don't parse it
                await r.aclose()
                raise httpx.DecodingError("unexpected stream on a non-streamed retry")
            data_bytes, first = await self._read(r, call.t0)
            return r, data_bytes, first

        try:
            resp, raw, ttfb = await attempt(body)
        except httpx.HTTPError:
            return resp, raw, ttfb  # keep the upstream's own error response
        if resp.status_code not in _RETRY_STATUSES:
            return resp, raw, ttfb
        model = data.get("model")
        fallback = self.config.fallback_models.get(model) if isinstance(model, str) else None
        if not fallback or not isinstance(model, str):
            return resp, raw, ttfb
        note = FailoverNote(
            time=utcnow(),
            from_model=model,
            to_model=fallback,
            status_code=resp.status_code,
            retries=1,
        )
        rewritten = json.dumps({**data, "model": fallback}, ensure_ascii=False).encode()
        try:
            resp, raw, ttfb = await attempt(rewritten)
        except httpx.HTTPError:
            return resp, raw, ttfb
        call.failover = note
        log.info(
            "gateway: failover %s -> %s after %d (%s)",
            model,
            fallback,
            note.status_code,
            provider,
        )
        return resp, raw, ttfb

    # ------------------------------------------------------------------ streams

    def _relay(
        self, provider: GatewayProvider, resp: httpx.Response, call: CallRecord | None
    ) -> Response:
        parser: StreamParser | None = None
        if call is not None:
            call.streamed = True
            call.status_code = resp.status_code
            parser = StreamParser(provider, resp.headers.get("content-encoding", ""))
            call.stream = parser

        async def chunks() -> AsyncIterator[bytes]:
            try:
                async for chunk in resp.aiter_raw():
                    if call is not None:
                        if call.ttfb_ms is None:
                            call.ttfb_ms = (time.perf_counter() - call.t0) * 1000
                        if parser is not None:
                            parser.feed(chunk)
                    yield chunk
                if call is not None:
                    call.completed = True
                    call.duration_ms = (time.perf_counter() - call.t0) * 1000
            except (asyncio.CancelledError, GeneratorExit):
                if call is not None and not call.completed:
                    call.disconnected = True
                raise
            except httpx.HTTPError as exc:
                # Upstream broke mid-stream: end the relay; the span records the error.
                log.warning("gateway: %s stream interrupted (%s)", provider, type(exc).__name__)
                if call is not None:
                    call.stream_error = type(exc).__name__
            finally:
                with anyio.CancelScope(shield=True):
                    await resp.aclose()

        on_done = partial(self._finish_stream, call) if call is not None else None
        return _RelayResponse(
            chunks(), resp.status_code, _response_headers(resp), resp.aclose, on_done
        )

    def _finish_stream(self, call: CallRecord) -> None:
        if not call.completed and call.stream_error is None:
            call.disconnected = True  # ended before the upstream finished: the client left
        self._finish(call)
