"""Reliability wired into the dispatch pipeline."""

from __future__ import annotations

from typing import ClassVar

import pytest

import contextlib

from atlas_mcp.config import ServerSettings
from atlas_mcp.context import RequestContext, dev_context, reset_context, set_context
from atlas_mcp.errors.framework import CircuitOpenError, UpstreamError
from atlas_mcp.reliability.circuit_breaker import State
from atlas_mcp.server import AtlasServer
from atlas_mcp.tools.base import Tool, ToolLevel, ToolMetadata
from atlas_mcp.validation.adversarial import StrictToolModel


class _Args(StrictToolModel):
    pass


class _FlakyTool(Tool):
    meta: ClassVar[ToolMetadata] = ToolMetadata(
        name="flaky.read",
        description="Fails transiently a set number of times.",
        level=ToolLevel.ATOMIC,
        scopes_required=(),
        destructive=False,
        timeout_ms=2_000,
    )
    input_schema: ClassVar[type[StrictToolModel]] = _Args

    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0

    async def run(self, tenant, args):  # type: ignore[override]
        self.calls += 1
        if self.calls <= self.fail_times:
            raise UpstreamError(code="transient", retryable=True, hint="down")
        return {"ok": True, "calls": self.calls}


def _server_with(tool: Tool) -> AtlasServer:
    server = AtlasServer(ServerSettings(circuit_breaker_failure_threshold=2))
    server.registry.register(tool)
    # Permit the demo tool through the policy engine.
    server.policy.check = lambda **kwargs: None  # type: ignore[method-assign]
    return server


def _ctx() -> RequestContext:
    return dev_context(tenant="acme", subject="tester", scopes=("tool:*:admin",))


@contextlib.contextmanager
def _active_context():
    token = set_context(_ctx())
    try:
        yield
    finally:
        reset_context(token)


@pytest.mark.asyncio
async def test_retry_recovers_transient_failure() -> None:
    tool = _FlakyTool(fail_times=2)
    server = _server_with(tool)
    with _active_context():
        envelope = _ctx().build_envelope("flaky.read", {})
        result = await server.dispatch(envelope)
    assert result["ok"] is True
    assert tool.calls == 3  # 2 failures + 1 success, retried within one dispatch


@pytest.mark.asyncio
async def test_circuit_opens_and_fast_fails() -> None:
    tool = _FlakyTool(fail_times=1000)  # always down
    server = _server_with(tool)

    with _active_context():
        envelope = _ctx().build_envelope("flaky.read", {})
        # threshold=2: two dispatches (each exhausting retries) trip the breaker.
        for _ in range(2):
            with pytest.raises(UpstreamError):
                await server.dispatch(envelope)

        assert server.breakers.for_tool("flaky.read").state is State.OPEN
        calls_before = tool.calls

        # Next dispatch must fast-fail without touching the backend.
        with pytest.raises(CircuitOpenError):
            await server.dispatch(envelope)
        assert tool.calls == calls_before
