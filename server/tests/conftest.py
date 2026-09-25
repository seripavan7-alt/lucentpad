"""Shared fixtures. DB tests use ``$PRISM_TEST_DATABASE_URL`` or a throwaway testcontainers
Postgres; every test (or module, for read-only suites) gets its own freshly created database."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio


def _default_docker_host() -> None:
    """testcontainers ignores ``docker context``; point it at OrbStack's socket if that is all
    there is (OrbStack doesn't create /var/run/docker.sock by default)."""
    orbstack = Path.home() / ".orbstack/run/docker.sock"
    if "DOCKER_HOST" not in os.environ and not Path("/var/run/docker.sock").exists():
        if orbstack.exists():
            os.environ["DOCKER_HOST"] = f"unix://{orbstack}"


@pytest.fixture(scope="session")
def pg_base_url() -> Iterator[str]:
    url = os.environ.get("PRISM_TEST_DATABASE_URL")
    if url:
        yield url
        return
    _default_docker_host()
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver=None) as pg:
        yield pg.get_connection_url()


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{name}"))


@asynccontextmanager
async def _fresh_database(base_url: str) -> AsyncIterator[str]:
    name = f"prism_test_{uuid4().hex[:12]}"
    admin = await asyncpg.connect(base_url)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        yield _with_database(base_url, name)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


@pytest_asyncio.fixture
async def db_url(pg_base_url: str) -> AsyncIterator[str]:
    """A brand-new, empty database for one test."""
    async with _fresh_database(pg_base_url) as url:
        yield url


@pytest_asyncio.fixture(scope="module")
async def module_db_url(pg_base_url: str) -> AsyncIterator[str]:
    """A brand-new database shared by one (read-only) test module."""
    async with _fresh_database(pg_base_url) as url:
        yield url
