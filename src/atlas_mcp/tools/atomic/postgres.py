"""Atomic Postgres query tool — Level 1 of the three-level hierarchy.

Exposes a narrowly-scoped read-only interface over Postgres. The tool
*refuses* to execute anything other than a SELECT/WITH. Write operations
would live in a separate tool with ``destructive=True`` and human approval.

Why separate read and write: agents routinely attempt to combine them when
given an unrestricted SQL surface. Splitting them turns a prompt injection
into a validation rejection instead of a dropped table.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import Field, field_validator

from atlas_mcp.config import get_settings
from atlas_mcp.errors.framework import UpstreamError
from atlas_mcp.tools.backends import PostgresBackend
from atlas_mcp.tools.base import Tool, ToolLevel, ToolMetadata
from atlas_mcp.validation.adversarial import StrictToolModel

_FORBIDDEN_KEYWORDS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "TRUNCATE",
    "ALTER",
    "CREATE",
    "GRANT",
    "REVOKE",
    "COPY",
    "CALL",
    "EXECUTE",
)


class PostgresQueryInput(StrictToolModel):
    sql: str = Field(..., description="A single SELECT or WITH statement.", max_length=8_000)
    params: list[Any] = Field(default_factory=list, description="Positional parameters.")
    max_rows: int = Field(100, ge=1, le=1000)

    @field_validator("sql")
    @classmethod
    def must_be_select(cls, value: str) -> str:
        stripped = value.strip().rstrip(";")
        if not stripped:
            raise ValueError("sql is required")
        head = stripped.split(None, 1)[0].upper()
        if head not in {"SELECT", "WITH"}:
            raise ValueError("only SELECT or WITH statements are permitted")
        upper = f" {stripped.upper()} "
        for kw in _FORBIDDEN_KEYWORDS:
            if f" {kw} " in upper:
                raise ValueError(f"forbidden keyword: {kw}")
        if ";" in stripped:
            raise ValueError("only one statement per call")
        return stripped


class PostgresQueryTool(Tool):
    meta: ClassVar[ToolMetadata] = ToolMetadata(
        name="postgres.query",
        description="Execute a single read-only SELECT against the Postgres warehouse.",
        level=ToolLevel.ATOMIC,
        scopes_required=("tool:postgres:read",),
        destructive=False,
        cacheable=True,
        cache_ttl_seconds=30,
        timeout_ms=5_000,
        tags=("postgres", "sql", "read"),
    )
    input_schema: ClassVar[type[StrictToolModel]] = PostgresQueryInput

    def __init__(self, backend: PostgresBackend | None = None) -> None:
        self._backend = backend

    def _get_backend(self) -> PostgresBackend:
        if self._backend is None:
            from atlas_mcp.tools.clients.postgres_client import AsyncpgBackend

            self._backend = AsyncpgBackend(
                dsn=get_settings().postgres_dsn,
                command_timeout=self.meta.timeout_ms / 1000,
            )
        return self._backend

    async def run(self, tenant: str, args: PostgresQueryInput) -> dict:  # type: ignore[override]
        try:
            rows = await self._get_backend().fetch(tenant, args.sql, args.params, args.max_rows)
        except UpstreamError:
            raise
        except Exception as exc:  # driver-specific errors are normalised here
            raise UpstreamError(
                code="postgres_error",
                retryable=_is_transient(exc),
                hint=str(exc).splitlines()[0][:200] if str(exc) else "postgres query failed",
                context={"sqlstate": getattr(exc, "sqlstate", None)},
            ) from exc

        return {
            "columns": list(rows[0].keys()) if rows else [],
            "rows": rows,
            "row_count": len(rows),
            "truncated": len(rows) >= args.max_rows,
        }


def _is_transient(exc: Exception) -> bool:
    name = type(exc).__name__
    return name in {
        "ConnectionDoesNotExistError",
        "InterfaceError",
        "TooManyConnectionsError",
        "ConnectionFailureError",
    }
