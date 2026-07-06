"""Tests for request context and tool call envelope."""

from __future__ import annotations

from atlas_mcp.context import (
    RequestContext,
    clear_context,
    current_context,
    dev_context,
    reset_context,
    set_context,
)
from atlas_mcp.validation.schemas import ToolCallEnvelope


def test_tool_call_envelope_fields() -> None:
    envelope = ToolCallEnvelope(
        tool="server.ping",
        arguments={"message": "hi"},
        tenant="acme",
        caller="agent:copilot",
        trace_id="trace-1",
        delegator="user:jordan",
    )
    assert envelope.tool == "server.ping"
    assert envelope.tenant == "acme"
    assert envelope.delegator == "user:jordan"


def test_build_envelope_from_context() -> None:
    ctx = RequestContext(
        subject="agent:copilot",
        tenant="acme",
        scopes=("tool:postgres:read",),
        delegator="user:jordan",
        trace_id="trace-2",
    )
    envelope = ctx.build_envelope("server.ping", {"message": "hi"})
    assert envelope == ToolCallEnvelope(
        tool="server.ping",
        arguments={"message": "hi"},
        tenant="acme",
        caller="agent:copilot",
        trace_id="trace-2",
        delegator="user:jordan",
    )


def test_current_context_defaults_without_set() -> None:
    clear_context()
    ctx = current_context()
    assert ctx.subject == "dev:local"
    assert ctx.tenant == "default"


def test_set_and_reset_context() -> None:
    clear_context()
    token = set_context(dev_context(tenant="globex", subject="agent:test"))
    assert current_context().tenant == "globex"
    reset_context(token)
    assert current_context().tenant == "default"
