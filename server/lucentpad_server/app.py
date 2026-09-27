"""FastAPI app. Route signatures here are the API contract (exported to contracts/openapi.json)."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError

from lucentpad_server import db, sample
from lucentpad_server.gateway import GatewayProxy
from lucentpad_server.gateway.config import GatewayConfig
from lucentpad_server.gateway.proxy import HttpGatewayProxy
from lucentpad_server.ingest import IngestPipeline
from lucentpad_server.ingest.body_limit import BodyLimitMiddleware, max_body_from_env
from lucentpad_server.ingest.queue import InProcessSpanQueue, queue_max_from_env
from lucentpad_server.ingest.writer import QueuedIngest, drain_timeout_from_env
from lucentpad_server.query import TraceFilter
from lucentpad_server.schema import (
    DataInfo,
    ErrorResponse,
    GatewayProvider,
    GatewaySummary,
    GatewayTurnList,
    IngestAccepted,
    IngestStats,
    SpanBatch,
    SpanSource,
    SpanStatus,
    TraceDetail,
    TraceFacets,
    TraceList,
    TraceOrder,
    TraceSort,
)
from lucentpad_server.store import InvalidCursorError, SpanStore

log = logging.getLogger(__name__)

_HEX = frozenset("0123456789abcdef")


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def _invalid(param: str, msg: str) -> RequestValidationError:
    return RequestValidationError([{"type": "value_error", "loc": ("query", param), "msg": msg}])


def _trace_filter(
    start: Annotated[
        datetime | None,
        Query(alias="from", description="Traces starting at or after this time (inclusive)."),
    ] = None,
    end: Annotated[
        datetime | None,
        Query(alias="to", description="Traces starting before this time (exclusive)."),
    ] = None,
    name: Annotated[list[str] | None, Query(description="Trace name; repeat for OR.")] = None,
    trace_status: Annotated[list[SpanStatus] | None, Query(alias="status")] = None,
    source: Annotated[list[SpanSource] | None, Query()] = None,
    client: Annotated[list[str] | None, Query()] = None,
    model: Annotated[
        list[str] | None, Query(description="Matches traces that used any of these models.")
    ] = None,
    service: Annotated[list[str] | None, Query(description="`service.name`.")] = None,
    hide_sample: Annotated[bool, Query(description="Leave out the startup sample data.")] = False,
) -> TraceFilter:
    """The shared filter parameters: OR within a filter, AND across filters."""
    for label, value in (("from", start), ("to", end)):
        if value is not None and value.tzinfo is None:
            raise _invalid(label, "timestamp must include a timezone")
    return TraceFilter(
        start=start,
        end=end,
        name=tuple(name or ()),
        status=tuple(trace_status or ()),
        source=tuple(source or ()),
        client=tuple(client or ()),
        model=tuple(model or ()),
        service=tuple(service or ()),
        hide_sample=hide_sample,
    )


def create_app(database_url: str | None = None, *, seed_sample: bool | None = None) -> FastAPI:
    """Build the app. Nothing connects here; the DB pool is opened in the lifespan.

    ``database_url`` defaults to ``$DATABASE_URL`` and ``seed_sample`` to
    ``$LUCENTPAD_SEED_SAMPLE=1``, both read at startup. Ingest settings (read at startup):
    ``$LUCENTPAD_INGEST_QUEUE_MAX`` (spans, default 50 000), ``$LUCENTPAD_INGEST_DRAIN_TIMEOUT``
    (seconds, default 5) and ``$LUCENTPAD_INGEST_MAX_BODY_BYTES`` (default 10 MiB, read here).
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        dsn = database_url or os.environ.get("DATABASE_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL is not set")
        pool = await db.create_pool(dsn)
        try:
            await db.migrate(pool)
            store = SpanStore(pool)
            seed = _env_flag("LUCENTPAD_SEED_SAMPLE") if seed_sample is None else seed_sample
            if seed and await store.is_empty():
                count = await store.insert_spans(sample.generate(datetime.now(UTC)), sample=True)
                log.info("loaded %d sample spans", count)
            shift = await store.shift_sample_to_now()
            if shift is not None:
                log.info("sample-only database: shifted sample timestamps by %s", shift)
            app.state.store = store
            ingest = QueuedIngest(store, InProcessSpanQueue(queue_max_from_env()))
            ingest.start()
            app.state.ingest = ingest
            # Gateway settings: see lucentpad_server/gateway/config.py (LUCENTPAD_* env vars).
            gateway = HttpGatewayProxy(GatewayConfig.from_env(), ingest)
            app.state.gateway = gateway
            try:
                yield
            finally:
                app.state.gateway = None
                await gateway.aclose()  # offers pending gateway spans before ingest drains
                app.state.ingest = None
                await ingest.stop(drain_timeout_from_env())
        finally:
            await pool.close()

    app = FastAPI(title="LucentPad API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        BodyLimitMiddleware, paths=frozenset({"/v1/spans"}), max_bytes=max_body_from_env()
    )

    def get_store(request: Request) -> SpanStore:
        store: SpanStore = request.app.state.store
        return store

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/data", tags=["meta"])
    async def data_info(request: Request) -> DataInfo:
        """Whether the database holds the startup sample data, real data, or both."""
        return await get_store(request).data_info()

    def get_ingest(request: Request) -> IngestPipeline:
        ingest: IngestPipeline | None = getattr(request.app.state, "ingest", None)
        if ingest is None:
            raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "ingest pipeline not running")
        return ingest

    @app.post(
        "/v1/spans",
        status_code=status.HTTP_202_ACCEPTED,
        tags=["ingest"],
        responses={
            413: {"model": ErrorResponse, "description": "Body larger than the ingest limit."},
            429: {
                "model": ErrorResponse,
                "description": "Ingest queue full; nothing from the batch was accepted. "
                "Retry after the `Retry-After` header (seconds).",
                "headers": {"Retry-After": {"schema": {"type": "integer"}}},
            },
        },
    )
    async def ingest_spans(batch: SpanBatch, request: Request) -> IngestAccepted:
        """Queue a batch for writing and return at once; spans are readable shortly after."""
        if not get_ingest(request).offer(batch.spans):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "ingest queue full",
                headers={"Retry-After": "1"},
            )
        return IngestAccepted(accepted=len(batch.spans))

    @app.get("/v1/ingest/stats", tags=["ingest"])
    async def ingest_stats(request: Request) -> IngestStats:
        return get_ingest(request).stats()

    @app.get(
        "/v1/traces",
        tags=["query"],
        responses={501: {"model": ErrorResponse, "description": "Filter not implemented yet."}},
    )
    async def list_traces(
        request: Request,
        filters: Annotated[TraceFilter, Depends(_trace_filter)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: Annotated[
            str | None, Query(description="`next_cursor` of the previous page.")
        ] = None,
        sort: Annotated[
            TraceSort,
            Query(description="Sort key; `order` sets the direction. The cursor encodes both."),
        ] = "started",
        order: TraceOrder = "desc",
        since: Annotated[
            datetime | None,
            Query(
                description="Live polling: only traces that gained spans after this time "
                "(the previous response's `as_of`). Includes a few seconds of overlap, so "
                "expect traces already seen and merge by `trace_id`. Not combinable with "
                "`cursor`."
            ),
        ] = None,
    ) -> TraceList:
        if since is not None and cursor is not None:
            raise _invalid("cursor", "cursor cannot be combined with since")
        if since is not None and since.tzinfo is None:
            raise _invalid("since", "timestamp must include a timezone")
        try:
            return await get_store(request).list_traces(
                filters, limit=limit, cursor=cursor, sort=sort, order=order, since=since
            )
        except InvalidCursorError:
            raise _invalid("cursor", "invalid cursor for this order and filter set") from None
        except NotImplementedError as exc:
            raise HTTPException(
                status.HTTP_501_NOT_IMPLEMENTED, str(exc) or "not implemented"
            ) from None

    @app.get(
        "/v1/traces/facets",
        tags=["query"],
        responses={501: {"model": ErrorResponse, "description": "Not implemented yet."}},
    )
    async def trace_facets(
        request: Request, filters: Annotated[TraceFilter, Depends(_trace_filter)]
    ) -> TraceFacets:
        try:
            return await get_store(request).facets(filters)
        except NotImplementedError as exc:
            raise HTTPException(
                status.HTTP_501_NOT_IMPLEMENTED, str(exc) or "not implemented"
            ) from None

    @app.get(
        "/v1/traces/{trace_id}",
        tags=["query"],
        responses={404: {"model": ErrorResponse}},
    )
    async def get_trace(
        trace_id: str,
        request: Request,
        since: Annotated[
            datetime | None,
            Query(
                description="Live polling: only spans stored after this time (the previous "
                "response's `as_of`), with a few seconds of overlap; merge by `span_id`. The "
                "summary is always current."
            ),
        ] = None,
    ) -> TraceDetail:
        if since is not None and since.tzinfo is None:
            raise _invalid("since", "timestamp must include a timezone")
        # A malformed trace_id can never match a stored trace, so it is a 404 (the only
        # error this route declares) rather than a 422.
        detail = None
        if len(trace_id) == 32 and set(trace_id) <= _HEX:
            detail = await get_store(request).get_trace(trace_id, since=since)
        if detail is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "trace not found")
        return detail

    # ------------------------------------------------------------------ gateway (M2)

    def get_gateway(request: Request) -> GatewayProxy:
        gateway: GatewayProxy | None = getattr(request.app.state, "gateway", None)
        if gateway is None:
            raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "gateway not running")
        return gateway

    async def _forward(provider: GatewayProvider, path: str, request: Request) -> Response:
        return await get_gateway(request).forward(provider, path, request)

    @app.post(
        "/gateway/anthropic/{path:path}",
        tags=["gateway"],
        operation_id="gateway_anthropic_post",
        summary="Anthropic API, proxied (Claude Code, Copilot CLI)",
        description="Set `ANTHROPIC_BASE_URL` (Claude Code) or `COPILOT_PROVIDER_BASE_URL` with "
        "`COPILOT_PROVIDER_TYPE=anthropic` (Copilot CLI) to `<host>/gateway/anthropic`. Forwards "
        "`v1/messages`, `v1/messages/count_tokens` and `v1/models`; bodies and streams pass "
        "through unchanged. The client's key is forwarded, never stored.",
        response_class=Response,
    )
    @app.get(
        "/gateway/anthropic/{path:path}",
        tags=["gateway"],
        operation_id="gateway_anthropic_get",
        summary="Anthropic API, proxied (Claude Code, Copilot CLI)",
        description="Set `ANTHROPIC_BASE_URL` (Claude Code) or `COPILOT_PROVIDER_BASE_URL` with "
        "`COPILOT_PROVIDER_TYPE=anthropic` (Copilot CLI) to `<host>/gateway/anthropic`. Forwards "
        "`v1/messages`, `v1/messages/count_tokens` and `v1/models`; bodies and streams pass "
        "through unchanged. The client's key is forwarded, never stored.",
        response_class=Response,
    )
    async def gateway_anthropic(path: str, request: Request) -> Response:
        return await _forward("anthropic", path, request)

    @app.post(
        "/gateway/openai/{path:path}",
        tags=["gateway"],
        operation_id="gateway_openai_post",
        summary="OpenAI API, proxied (Copilot Chat Custom Endpoint, Copilot CLI)",
        description="Use `<host>/gateway/openai/v1` as the OpenAI-compatible base URL. Forwards "
        "`v1/chat/completions` and `v1/models`; bodies and streams pass through unchanged.",
        response_class=Response,
    )
    @app.get(
        "/gateway/openai/{path:path}",
        tags=["gateway"],
        operation_id="gateway_openai_get",
        summary="OpenAI API, proxied (Copilot Chat Custom Endpoint, Copilot CLI)",
        description="Use `<host>/gateway/openai/v1` as the OpenAI-compatible base URL. Forwards "
        "`v1/chat/completions` and `v1/models`; bodies and streams pass through unchanged.",
        response_class=Response,
    )
    async def gateway_openai(path: str, request: Request) -> Response:
        return await _forward("openai", path, request)

    @app.get(
        "/v1/gateway/turns",
        tags=["query"],
        responses={501: {"model": ErrorResponse, "description": "Not implemented yet."}},
    )
    async def gateway_turns(
        request: Request,
        start: Annotated[datetime | None, Query(alias="from")] = None,
        end: Annotated[datetime | None, Query(alias="to")] = None,
        client: Annotated[list[str] | None, Query(description="Repeat for OR.")] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: str | None = None,
        since: Annotated[
            datetime | None,
            Query(description="Live polling: turns stored after this time (previous `as_of`)."),
        ] = None,
        hide_sample: Annotated[
            bool, Query(description="Leave out the startup sample data.")
        ] = False,
    ) -> GatewayTurnList:
        """Gateway model calls, newest first."""
        for label, value in (("from", start), ("to", end), ("since", since)):
            if value is not None and value.tzinfo is None:
                raise _invalid(label, "timestamp must include a timezone")
        if since is not None and cursor is not None:
            raise _invalid("cursor", "cursor cannot be combined with since")
        try:
            return await get_store(request).gateway_turns(
                start=start,
                end=end,
                clients=tuple(client or ()),
                limit=limit,
                cursor=cursor,
                since=since,
                hide_sample=hide_sample,
            )
        except InvalidCursorError:
            raise _invalid("cursor", "invalid cursor") from None
        except NotImplementedError as exc:
            raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, str(exc)) from None

    @app.get(
        "/v1/gateway/summary",
        tags=["query"],
        responses={501: {"model": ErrorResponse, "description": "Not implemented yet."}},
    )
    async def gateway_summary(
        request: Request,
        start: Annotated[datetime | None, Query(alias="from")] = None,
        end: Annotated[datetime | None, Query(alias="to")] = None,
        hide_sample: Annotated[
            bool, Query(description="Leave out the startup sample data.")
        ] = False,
    ) -> GatewaySummary:
        """Per-client sessions, turns, tokens and cost over the window."""
        for label, value in (("from", start), ("to", end)):
            if value is not None and value.tzinfo is None:
                raise _invalid(label, "timestamp must include a timezone")
        try:
            return await get_store(request).gateway_summary(
                start=start, end=end, hide_sample=hide_sample
            )
        except NotImplementedError as exc:
            raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, str(exc)) from None

    return app
