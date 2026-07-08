"""Structured Error Recovery Framework."""

from atlas_mcp.errors.framework import (
    AuthError,
    CircuitOpenError,
    PolicyError,
    RateLimitError,
    TimeoutError_,
    ToolError,
    ToolNotFoundError,
    UpstreamError,
    ValidationError,
    as_call_tool_result,
    normalise_exception,
    to_call_tool_error,
    to_mcp_error,
)

__all__ = [
    "AuthError",
    "CircuitOpenError",
    "PolicyError",
    "RateLimitError",
    "TimeoutError_",
    "ToolError",
    "ToolNotFoundError",
    "UpstreamError",
    "ValidationError",
    "as_call_tool_result",
    "normalise_exception",
    "to_call_tool_error",
    "to_mcp_error",
]
