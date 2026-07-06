"""Structured error types surfaced to MCP clients."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ToolError(Exception):
    """Base class for errors surfaced through the dispatch pipeline."""

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


class ValidationError(ToolError):
    """Input failed schema or constraint validation."""


class AuthError(ToolError):
    """Token missing, expired, or invalid."""


class ToolNotFoundError(ToolError):
    """Requested tool name is not registered."""

    def __init__(self, name: str) -> None:
        super().__init__(
            code="tool_not_found",
            retryable=False,
            hint=f"no tool named {name!r} is registered",
            context={"tool": name},
        )


def to_call_tool_error(exc: ToolError):
    """Convert a :class:`ToolError` into an MCP ``CallToolResult``."""
    from mcp.types import CallToolResult, TextContent

    return CallToolResult(
        content=[TextContent(type="text", text=f"{exc.code}: {exc.hint or ''}")],
        structuredContent=exc.to_dict(),
        isError=True,
    )
