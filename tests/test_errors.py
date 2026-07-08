"""Structured Error Recovery Framework — wire format and retry semantics."""

from __future__ import annotations

from atlas_mcp.errors.framework import (
    AuthError,
    CircuitOpenError,
    RateLimitError,
    TimeoutError_,
    ToolError,
    UpstreamError,
    ValidationError,
    to_call_tool_error,
    to_mcp_error,
)


def test_retryable_flag_propagates_through_to_dict() -> None:
    exc = UpstreamError(code="es_timeout", retryable=True, hint="elasticsearch slow")
    payload = exc.to_dict()
    assert payload["code"] == "es_timeout"
    assert payload["retryable"] is True
    assert "elasticsearch" in payload["hint"]


def test_rate_limit_carries_retry_after() -> None:
    exc = RateLimitError(retry_after_seconds=2.5)
    assert exc.retryable is True
    assert exc.context["retry_after_seconds"] == 2.5
    assert "2.5" in exc.hint


def test_circuit_open_error_has_tool_and_recovery() -> None:
    exc = CircuitOpenError(tool="postgres.query", recovery_seconds=30)
    assert exc.context["tool"] == "postgres.query"
    assert exc.context["recovery_seconds"] == 30
    assert exc.code == "circuit_open"
    assert exc.retryable is True


def test_timeout_error_includes_budget() -> None:
    exc = TimeoutError_(tool="server.ping", budget_ms=5000)
    assert exc.code == "timeout"
    assert exc.context["budget_ms"] == 5000
    assert "5000" in exc.hint


def test_mcp_wire_format_includes_serf_payload() -> None:
    exc = AuthError(code="token_expired", retryable=True, hint="refresh")
    mcp_err = to_mcp_error(exc)
    assert mcp_err.code == -32000
    assert "token_expired" in mcp_err.message
    assert mcp_err.data["retryable"] is True
    assert mcp_err.data["hint"] == "refresh"


def test_call_tool_error_is_json_parseable() -> None:
    exc = ValidationError(
        code="invalid_arguments",
        retryable=False,
        hint="message: String should have at most 200 characters",
        context={"tool": "server.ping"},
    )
    result = to_call_tool_error(exc)
    assert result.isError is True
    assert result.structuredContent["code"] == "invalid_arguments"
    assert result.structuredContent["retryable"] is False
    assert "hint" in result.structuredContent


def test_tool_error_str_is_agent_readable() -> None:
    exc = ToolError(code="boom", hint="try something else")
    assert str(exc) == "boom: try something else"
