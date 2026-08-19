"""Async PostgreSQL connection pool (psycopg v3)."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from asapbackend.config import settings

_pool: AsyncConnectionPool | None = None


async def _configure(conn: psycopg.AsyncConnection) -> None:
    # Autocommit every statement immediately so writes are visible to other
    # connections without waiting for the request to finish. Long-running SSE
    # streams would otherwise hold an idle-in-transaction connection for the
    # entire duration of LLM inference.
    # Explicit multi-statement transactions are still possible via:
    #   async with conn.transaction(): ...
    await conn.set_autocommit(True)


async def open_pool() -> None:
    global _pool
    _pool = AsyncConnectionPool(
        conninfo=settings.database_url,
        min_size=2,
        max_size=10,
        kwargs={"row_factory": dict_row},
        configure=_configure,
        open=False,
    )
    await _pool.open()


async def close_pool() -> None:
    if _pool:
        await _pool.close()


@asynccontextmanager
async def get_connection() -> AsyncIterator[psycopg.AsyncConnection]:
    if _pool is None:
        raise RuntimeError("Database pool is not initialised.")
    async with _pool.connection() as conn:
        yield conn


async def get_db() -> AsyncIterator[psycopg.AsyncConnection]:
    """FastAPI dependency that yields a database connection."""
    async with get_connection() as conn:
        yield conn
