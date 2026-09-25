"""Postgres connection pool and a minimal migration runner (plain SQL files, applied in order)."""

from __future__ import annotations

import json
import logging
from importlib import resources
from typing import Any

import asyncpg

log = logging.getLogger(__name__)

# Arbitrary constant: serialises concurrent migration runners (e.g. two API replicas).
_MIGRATION_LOCK_ID = 0x4C55_4345_4E54  # "LUCENT"


_JSONB_VERSION = b"\x01"  # jsonb binary wire format: a version byte, then the JSON text


def _encode_jsonb(value: Any) -> bytes:
    return _JSONB_VERSION + json.dumps(value, separators=(",", ":"), allow_nan=False).encode()


def _decode_jsonb(data: bytes) -> Any:
    return json.loads(data[1:])


async def _init_connection(conn: asyncpg.Connection) -> None:
    # Binary codec so jsonb also works with COPY (copy_records_to_table is binary-only).
    await conn.set_type_codec(
        "jsonb", encoder=_encode_jsonb, decoder=_decode_jsonb, schema="pg_catalog", format="binary"
    )
    await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def create_pool(dsn: str, *, min_size: int = 1, max_size: int = 10) -> asyncpg.Pool:
    """Create an asyncpg pool whose json/jsonb columns map to Python objects."""
    return await asyncpg.create_pool(
        dsn, min_size=min_size, max_size=max_size, init=_init_connection
    )


def migration_files() -> list[tuple[str, str]]:
    """(version, sql) for every ``migrations/NNNN_*.sql`` file, sorted by version."""
    root = resources.files("lucentpad_server") / "migrations"
    files = sorted(
        (entry.name, entry.read_text(encoding="utf-8"))
        for entry in root.iterdir()
        if entry.name.endswith(".sql")
    )
    return [(name.removesuffix(".sql"), sql) for name, sql in files]


async def migrate(pool: asyncpg.Pool) -> list[str]:
    """Apply unapplied migrations in order, one transaction each. Returns the versions applied."""
    applied_now: list[str] = []
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", _MIGRATION_LOCK_ID)
        try:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version    text        PRIMARY KEY,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            done = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}
            for version, sql in migration_files():
                if version in done:
                    continue
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        "INSERT INTO schema_migrations (version) VALUES ($1)", version
                    )
                log.info("applied migration %s", version)
                applied_now.append(version)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _MIGRATION_LOCK_ID)
    return applied_now
