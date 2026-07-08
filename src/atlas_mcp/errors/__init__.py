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
    "to_call_tool_error",
    "to_mcp_error",
]
