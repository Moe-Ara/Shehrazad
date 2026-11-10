"""SQLite access (aiosqlite) with a simple, ordered migration runner.

SQLite in WAL mode is plenty for a single bot process plus the operator CLI.
The schema avoids SQLite-only features where practical so a move to
PostgreSQL stays mechanical (see docs/operator-guide.md).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from importlib import resources
from pathlib import Path
from typing import Any

import aiosqlite

from .util import to_iso, utcnow

log = logging.getLogger(__name__)

_MIGRATION_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


def discover_migrations() -> list[tuple[int, str, str]]:
    found = []
    for entry in resources.files("musicbot.migrations").iterdir():
        match = _MIGRATION_NAME.match(entry.name)
        if match:
            found.append((int(match.group(1)), match.group(2), entry.read_text(encoding="utf-8")))
    found.sort()
    versions = [v for v, _, _ in found]
    if len(set(versions)) != len(versions):
        raise RuntimeError("duplicate migration version numbers")
    return found


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path) if str(path) != ":memory:" else path
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("database is not connected")
        return self._conn

    async def connect(self, *, migrate: bool = True) -> None:
        if isinstance(self.path, Path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self.path))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        if migrate:
            await self.migrate()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def migrate(self) -> list[int]:
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        await self.conn.commit()
        async with self.conn.execute("SELECT version FROM schema_migrations") as cur:
            applied = {row[0] async for row in cur}
        newly_applied = []
        async with self._write_lock:
            for version, name, sql in discover_migrations():
                if version in applied:
                    continue
                log.info("applying migration %04d_%s", version, name)
                # executescript commits first; wrap the script so it is atomic.
                script = (
                    "BEGIN;\n"
                    f"{sql}\n"
                    "INSERT INTO schema_migrations (version, name, applied_at) "
                    f"VALUES ({version}, '{name}', '{to_iso(utcnow())}');\n"
                    "COMMIT;"
                )
                try:
                    await self.conn.executescript(script)
                except Exception:
                    await self.conn.rollback()
                    raise
                newly_applied.append(version)
        return newly_applied

    async def schema_version(self) -> int:
        row = await self.fetchone("SELECT MAX(version) AS v FROM schema_migrations")
        return int(row["v"] or 0) if row else 0

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        async with self._write_lock:
            cursor = await self.conn.execute(sql, params)
            await self.conn.commit()
            rowcount = cursor.rowcount
            await cursor.close()
            return rowcount

    async def insert(self, sql: str, params: Sequence[Any] = ()) -> int:
        async with self._write_lock:
            cursor = await self.conn.execute(sql, params)
            await self.conn.commit()
            last_id = cursor.lastrowid or 0
            await cursor.close()
            return last_id

    async def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        async with self._write_lock:
            await self.conn.executemany(sql, rows)
            await self.conn.commit()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                await self.conn.rollback()
                raise
            else:
                await self.conn.commit()

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, params) as cursor:
            return await cursor.fetchone()

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, params) as cursor:
            return list(await cursor.fetchall())

    async def ping(self) -> bool:
        try:
            await self.fetchone("SELECT 1")
            return True
        except Exception:
            return False
