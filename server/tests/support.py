"""Test helpers shared across modules."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


@asynccontextmanager
async def app_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """An httpx client over ASGI with the app's lifespan (DB pool, migrations) running."""
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        yield c
