"""Async SQLite connection management (WAL).

Single-user local gateway: one connection guarded by an asyncio lock, explicit
``BEGIN IMMEDIATE`` transactions around reservation writes. Connections are
re-opened when the running event loop changes so tests can use isolated
databases without leaking state between loops.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

from app.db.schema import ALL_DDL, SCHEMA_VERSION

logger = logging.getLogger(__name__)

MEMORY_PATH = ":memory:"


class Database:
    """Thin async wrapper around aiosqlite with transaction helpers."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def is_memory(self) -> bool:
        return self.path in (MEMORY_PATH, "") or self.path.startswith("file::memory:")

    async def connection(self) -> aiosqlite.Connection:
        loop = asyncio.get_running_loop()
        if self._conn is not None and self._loop is not loop:
            await self._discard()
        if self._conn is None:
            self._conn = await self._open(loop)
        return self._conn

    async def _open(self, loop: asyncio.AbstractEventLoop) -> aiosqlite.Connection:
        if not self.is_memory:
            parent = Path(self.path).expanduser().resolve().parent
            parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self.path, isolation_level=None)
        conn.row_factory = aiosqlite.Row
        if not self.is_memory:
            await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA busy_timeout=5000")
        for statement in ALL_DDL:
            await conn.execute(statement)
        await conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self._loop = loop
        return conn

    async def _discard(self) -> None:
        conn, self._conn, self._loop = self._conn, None, None
        if conn is None:
            return
        try:
            await conn.close()
        except Exception:  # noqa: BLE001 - closing a stale loop's connection is best effort
            logger.debug("Ignoring error while closing stale SQLite connection")

    async def close(self) -> None:
        async with self._lock:
            await self._discard()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Exclusive write transaction. Serialized per process by ``self._lock``."""
        conn = await self.connection()
        async with self._lock:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                try:
                    await conn.execute("ROLLBACK")
                except Exception:  # noqa: BLE001
                    logger.debug("Rollback failed", exc_info=True)
                raise
            await conn.execute("COMMIT")

    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[aiosqlite.Row]:
        conn = await self.connection()
        async with self._lock:
            cursor = await conn.execute(sql, tuple(params))
            try:
                return list(await cursor.fetchall())
            finally:
                await cursor.close()

    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Row | None:
        conn = await self.connection()
        async with self._lock:
            cursor = await conn.execute(sql, tuple(params))
            try:
                return await cursor.fetchone()
            finally:
                await cursor.close()

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run a single statement. Returns the affected row count."""
        async with self.transaction() as conn:
            cursor = await conn.execute(sql, tuple(params))
            try:
                rowcount = cursor.rowcount
            finally:
                await cursor.close()
        return max(rowcount, 0)
