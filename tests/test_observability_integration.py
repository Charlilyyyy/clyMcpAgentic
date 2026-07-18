"""Observability wired into dispatch — metrics recorded, audit trail written."""

from __future__ import annotations

import contextlib
from typing import ClassVar

import pytest

from atlas_mcp.config import ServerSettings
from atlas_mcp.context import RequestContext, dev_context, reset_context, set_context
from atlas_mcp.server import AtlasServer
from atlas_mcp.tools.base import Tool, ToolLevel, ToolMetadata
from atlas_mcp.validation.adversarial import StrictToolModel


class _Args(StrictToolModel):
    q: str


class _EchoTool(Tool):
    meta: ClassVar[ToolMetadata] = ToolMetadata(
        name="echo.read",
        description="Echoes the query.",
        level=ToolLevel.ATOMIC,
        scopes_required=(),
        destructive=False,
        cacheable=True,
        cache_ttl_seconds=60,
    )
    input_schema: ClassVar[type[StrictToolModel]] = _Args

    async def run(self, tenant, args):  # type: ignore[override]
        return {"echo": args.q}


def _ctx() -> RequestContext:
    return dev_context(
        tenant="acme", subject="agent:7", scopes=("tool:*:admin",), trace_id="trace-xyz"
    )


@contextlib.contextmanager
def _active_context():
    token = set_context(_ctx())
    try:
        yield
    finally:
        reset_context(token)


@pytest.mark.asyncio
async def test_dispatch_records_metrics_and_audit(tmp_path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    server = AtlasServer(ServerSettings(audit_log_path=str(audit_path)))
    server.registry.register(_EchoTool())
    server.policy.check = lambda **kwargs: None  # type: ignore[method-assign]

    with _active_context():
        envelope = _ctx().build_envelope("echo.read", {"q": "hi"})
        await server.dispatch(envelope)  # miss → executes
        await server.dispatch(envelope)  # hit → cached

    rendered = server.metrics.render().decode()
    assert 'atlas_tool_calls_total{status="ok",tool="echo.read"} 2.0' in rendered
    assert 'atlas_cache_hits_total{tool="echo.read"} 1.0' in rendered
    assert 'atlas_cache_misses_total{tool="echo.read"} 1.0' in rendered

    # Audit answers "who called echo.read for tenant acme?"
    events = server.audit.query(tenant="acme", tool="echo.read")
    assert len(events) == 2
    assert events[0]["caller"] == "agent:7"
    assert events[0]["trace_id"] == "trace-xyz"
    assert events[0]["status"] == "ok"
    # Arguments are hashed, never stored verbatim.
    assert "hi" not in audit_path.read_text()
