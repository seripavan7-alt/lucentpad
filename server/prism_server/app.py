"""FastAPI app. Route signatures here are the API contract (exported to contracts/openapi.json)."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError

from prism_server import db, sample
from prism_server.schema import (
    ErrorResponse,
    IngestAccepted,
    SpanBatch,
    SpanSource,
    SpanStatus,
    TraceDetail,
    TraceList,
)
from prism_server.store import Cursor, InvalidCursorError, SpanStore

log = logging.getLogger(__name__)

_HEX = frozenset("0123456789abcdef")


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def create_app(database_url: str | None = None, *, seed_sample: bool | None = None) -> FastAPI:
    """Build the app. Nothing connects here; the DB pool is opened in the lifespan.

    ``database_url`` defaults to ``$DATABASE_URL`` and ``seed_sample`` to
    ``$PRISM_SEED_SAMPLE=1``, both read at startup.
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
            seed = _env_flag("PRISM_SEED_SAMPLE") if seed_sample is None else seed_sample
            if seed and await store.is_empty():
                count = await store.insert_spans(sample.generate(datetime.now(UTC)))
                log.info("loaded %d sample spans", count)
            app.state.store = store
            yield
        finally:
            await pool.close()

    app = FastAPI(title="Prism API", version="0.1.0", lifespan=lifespan)

    def get_store(request: Request) -> SpanStore:
        store: SpanStore = request.app.state.store
        return store

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/v1/spans",
        status_code=status.HTTP_202_ACCEPTED,
        tags=["ingest"],
        responses={
            429: {"model": ErrorResponse, "description": "Ingest queue full; retry later."},
            501: {"model": ErrorResponse, "description": "Not implemented until M1."},
        },
    )
    async def ingest_spans(batch: SpanBatch) -> IngestAccepted:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "ingest arrives in M1")

    @app.get("/v1/traces", tags=["query"])
    async def list_traces(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: str | None = None,
        source: SpanSource | None = None,
        trace_status: Annotated[SpanStatus | None, Query(alias="status")] = None,
    ) -> TraceList:
        try:
            after = Cursor.decode(cursor) if cursor is not None else None
        except InvalidCursorError:
            raise RequestValidationError(
                [{"type": "value_error", "loc": ("query", "cursor"), "msg": "invalid cursor"}]
            ) from None
        traces, next_page = await get_store(request).list_traces(
            limit=limit, cursor=after, source=source, status=trace_status
        )
        return TraceList(
            traces=traces, next_cursor=next_page.encode() if next_page is not None else None
        )

    @app.get(
        "/v1/traces/{trace_id}",
        tags=["query"],
        responses={404: {"model": ErrorResponse}},
    )
    async def get_trace(trace_id: str, request: Request) -> TraceDetail:
        # A malformed trace_id can never match a stored trace, so it is a 404 (the only
        # error this route declares) rather than a 422.
        detail = None
        if len(trace_id) == 32 and set(trace_id) <= _HEX:
            detail = await get_store(request).get_trace(trace_id)
        if detail is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "trace not found")
        return detail

    return app
