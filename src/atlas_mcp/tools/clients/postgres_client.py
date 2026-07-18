"""Real asyncpg-backed Postgres adapter.

Lazily imported by :class:`~atlas_mcp.tools.atomic.postgres.PostgresQueryTool`
so that unit tests using an injected fake never import asyncpg.
"""

from __future__ import annotations

from typing import Any

import asyncpg


class AsyncpgBackend:
    """Connection-pooled Postgres backend with per-query tenant scoping."""

    def __init__(self, dsn: str, command_timeout: float = 5.0) -> None:
        self._dsn = dsn
        self._command_timeout = command_timeout
        self._pool: asyncpg.Pool | None = None

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn,
                min_size=2,
                max_size=10,
                command_timeout=self._command_timeout,
            )
        return self._pool

    async def fetch(
        self, tenant: str, sql: str, params: list[Any], max_rows: int
    ) -> list[dict[str, Any]]:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            # Row-level security reads app.tenant_id; set it before every query.
            await conn.execute("SET LOCAL app.tenant_id = $1", tenant)
            rows = await conn.fetch(f"{sql} LIMIT {max_rows}", *params)
        return [dict(r) for r in rows]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
