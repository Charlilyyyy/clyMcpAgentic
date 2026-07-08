"""Structured Error Recovery Framework (SERF).

Agents cannot recover from Python tracebacks or plain English strings.
What they can recover from is a small, stable vocabulary that tells them:

* what went wrong (``code``),
* whether retrying will help (``retryable``),
* and what to try differently (``hint``).

Every other component raises one of these error types; nothing else leaks
to the agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mcp.types import ErrorData


@dataclass
class ToolError(Exception):
    """Base class for every error Atlas-MCP surfaces to an agent."""

    code: str
    retryable: bool = False
    hint: str | None = None
    context: dict[str, Any] | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.hint or ''}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "retryable": self.retryable,
            "hint": self.hint,
            "context": self.context or {},
        }


class AuthError(ToolError):
    """Token missing, expired, or invalid."""


class PolicyError(ToolError):
    """The caller is authenticated but not permitted."""


class ValidationError(ToolError):
    """Input failed schema or constraint validation."""


class RateLimitError(ToolError):
    """Per-tenant or per-tool quota exceeded."""

    def __init__(self, retry_after_seconds: float, hint: str | None = None):
        super().__init__(
            code="rate_limited",
            retryable=True,
            hint=hint or f"retry after {retry_after_seconds:.1f}s",
            context={"retry_after_seconds": retry_after_seconds},
        )


class UpstreamError(ToolError):
    """An upstream system (Postgres, Elasticsearch, S3) failed.

    ``retryable`` is True for transient failures (timeout, 503) and False for
    deterministic failures (syntax error, 400).
    """


class CircuitOpenError(ToolError):
    """Circuit breaker is open — stop calling this tool for now."""

    def __init__(self, tool: str, recovery_seconds: int):
        super().__init__(
            code="circuit_open",
            retryable=True,
            hint=f"tool {tool!r} is temporarily disabled; retry after {recovery_seconds}s",
            context={"tool": tool, "recovery_seconds": recovery_seconds},
        )


class TimeoutError_(ToolError):  # trailing underscore avoids shadowing builtin
    """ATBA budget or per-tool timeout was exhausted."""

    def __init__(self, tool: str, budget_ms: int):
        super().__init__(
            code="timeout",
            retryable=True,
            hint=f"tool {tool!r} exceeded {budget_ms}ms budget",
            context={"tool": tool, "budget_ms": budget_ms},
        )


class ToolNotFoundError(ToolError):
    """Requested tool name is not registered."""

    def __init__(self, name: str) -> None:
        super().__init__(
            code="tool_not_found",
            retryable=False,
            hint=f"no tool named {name!r} is registered",
            context={"tool": name},
        )


def to_mcp_error(exc: ToolError) -> ErrorData:
    """Convert an Atlas :class:`ToolError` into the MCP JSON-RPC error shape."""
    return ErrorData(
        code=-32000,
        message=f"{exc.code}: {exc.hint or ''}",
        data=exc.to_dict(),
    )


def to_call_tool_error(exc: ToolError):
    """Convert a :class:`ToolError` into an MCP ``CallToolResult``."""
    from mcp.types import CallToolResult, TextContent

    return CallToolResult(
        content=[TextContent(type="text", text=f"{exc.code}: {exc.hint or ''}")],
        structuredContent=exc.to_dict(),
        isError=True,
    )
