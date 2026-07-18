"""Atomic Postgres tool — SELECT-only guard and result shaping."""

from __future__ import annotations

from typing import Any

import pytest

from atlas_mcp.errors.framework import UpstreamError, ValidationError
from atlas_mcp.tools.atomic.postgres import PostgresQueryTool


class _FakePostgres:
    def __init__(self, rows: list[dict[str, Any]] | None = None, error: Exception | None = None):
        self.rows = rows or []
        self.error = error
        self.calls: list[tuple[str, str, list, int]] = []

    async def fetch(self, tenant, sql, params, max_rows):
        self.calls.append((tenant, sql, params, max_rows))
        if self.error is not None:
            raise self.error
        return self.rows[:max_rows]


def _tool(**kwargs) -> PostgresQueryTool:
    return PostgresQueryTool(backend=_FakePostgres(**kwargs))


@pytest.mark.asyncio
async def test_select_returns_shaped_rows() -> None:
    tool = _tool(rows=[{"id": 1, "name": "acme"}, {"id": 2, "name": "globex"}])
    args = tool.validate({"sql": "SELECT id, name FROM customers", "max_rows": 10})
    result = await tool.run("acme", args)
    assert result["columns"] == ["id", "name"]
    assert result["row_count"] == 2
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_tenant_is_passed_to_backend() -> None:
    backend = _FakePostgres(rows=[{"id": 1}])
    tool = PostgresQueryTool(backend=backend)
    args = tool.validate({"sql": "SELECT id FROM orders"})
    await tool.run("globex", args)
    assert backend.calls[0][0] == "globex"


@pytest.mark.asyncio
async def test_truncated_flag_when_max_rows_hit() -> None:
    tool = _tool(rows=[{"id": i} for i in range(5)])
    args = tool.validate({"sql": "SELECT id FROM orders", "max_rows": 5})
    result = await tool.run("acme", args)
    assert result["truncated"] is True


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE customers",
        "UPDATE customers SET name = 'x'",
        "DELETE FROM customers",
        "SELECT 1; DROP TABLE customers",
        "INSERT INTO customers VALUES (1)",
    ],
)
def test_rejects_non_select_statements(sql: str) -> None:
    tool = _tool()
    with pytest.raises(ValidationError):
        tool.validate({"sql": sql})


def test_allows_with_cte() -> None:
    tool = _tool()
    args = tool.validate({"sql": "WITH t AS (SELECT 1) SELECT * FROM t"})
    assert args.sql.startswith("WITH")


@pytest.mark.asyncio
async def test_backend_failure_becomes_upstream_error() -> None:
    tool = _tool(error=RuntimeError("connection reset"))
    args = tool.validate({"sql": "SELECT 1"})
    with pytest.raises(UpstreamError) as exc_info:
        await tool.run("acme", args)
    assert exc_info.value.code == "postgres_error"
    assert "connection reset" in (exc_info.value.hint or "")
