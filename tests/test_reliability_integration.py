"""Reliability integration — outage → open → half-open recovery through dispatch."""

from __future__ import annotations

import asyncio
import contextlib
from typing import ClassVar

import pytest

from atlas_mcp.config import ServerSettings
from atlas_mcp.context import RequestContext, dev_context, reset_context, set_context
from atlas_mcp.errors.framework import CircuitOpenError, UpstreamError
from atlas_mcp.reliability.circuit_breaker import State
from atlas_mcp.server import AtlasServer
from atlas_mcp.tools.base import Tool, ToolLevel, ToolMetadata
from atlas_mcp.validation.adversarial import StrictToolModel


class _Args(StrictToolModel):
    pass


class _OutageTool(Tool):
    """Down while ``healthy`` is False; recovers when flipped True."""

    meta: ClassVar[ToolMetadata] = ToolMetadata(
        name="outage.read",
        description="Toggleable backend for outage simulation.",
        level=ToolLevel.ATOMIC,
        scopes_required=(),
        destructive=False,
        timeout_ms=2_000,
    )
    input_schema: ClassVar[type[StrictToolModel]] = _Args

    def __init__(self) -> None:
        self.healthy = False

    async def run(self, tenant, args):  # type: ignore[override]
        if not self.healthy:
            raise UpstreamError(code="transient", retryable=True, hint="backend down")
        return {"ok": True}


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
async def test_outage_opens_then_half_open_recovers() -> None:
    tool = _OutageTool()
    server = AtlasServer(
        ServerSettings(
            circuit_breaker_failure_threshold=2,
            circuit_breaker_recovery_seconds=1,
            retry_max_attempts=1,  # no retry sleeps — one attempt per dispatch
        )
    )
    server.registry.register(tool)
    server.policy.check = lambda **kwargs: None  # type: ignore[method-assign]
    breaker = server.breakers.for_tool("outage.read")

    with _active_context():
        envelope = _ctx().build_envelope("outage.read", {})

        # 1. Outage: two failures trip the breaker.
        for _ in range(2):
            with pytest.raises(UpstreamError):
                await server.dispatch(envelope)
        assert breaker.state is State.OPEN

        # 2. Open circuit fast-fails without hitting the dead backend.
        with pytest.raises(CircuitOpenError):
            await server.dispatch(envelope)

        # 3. Backend recovers; wait past the recovery window for a half-open probe.
        tool.healthy = True
        await asyncio.sleep(1.1)
        result = await server.dispatch(envelope)
        assert result == {"ok": True}
        assert breaker.state is State.CLOSED

    snapshot = server.reliability_snapshot()
    assert snapshot["breakers"]["outage.read"]["trips"] == 1
    assert snapshot["breaker_short_circuits"] == 1
    assert snapshot["per_tool_calls"]["outage.read"] >= 3
