"""Cache serves reads during a downstream outage (cache-before-breaker)."""

from __future__ import annotations

import contextlib
from typing import ClassVar

import pytest

from atlas_mcp.config import ServerSettings
from atlas_mcp.context import RequestContext, dev_context, reset_context, set_context
from atlas_mcp.errors.framework import UpstreamError
from atlas_mcp.server import AtlasServer
from atlas_mcp.tools.base import Tool, ToolLevel, ToolMetadata
from atlas_mcp.validation.adversarial import StrictToolModel


class _Args(StrictToolModel):
    pass


class _ToggleTool(Tool):
    meta: ClassVar[ToolMetadata] = ToolMetadata(
        name="warm.read",
        description="Serves once, then goes down.",
        level=ToolLevel.ATOMIC,
        scopes_required=(),
        destructive=False,
        cacheable=True,
        cache_ttl_seconds=300,
    )
    input_schema: ClassVar[type[StrictToolModel]] = _Args

    def __init__(self) -> None:
        self.healthy = True

    async def run(self, tenant, args):  # type: ignore[override]
        if not self.healthy:
            raise UpstreamError(code="transient", retryable=True, hint="down")
        return {"value": "warm"}


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
async def test_warm_cache_survives_backend_outage() -> None:
    tool = _ToggleTool()
    server = AtlasServer(ServerSettings())
    server.registry.register(tool)
    server.policy.check = lambda **kwargs: None  # type: ignore[method-assign]

    with _active_context():
        envelope = _ctx().build_envelope("warm.read", {})
        first = await server.dispatch(envelope)  # populates cache
        assert first == {"value": "warm"}

        # Backend goes down; the cached read is still served without error.
        tool.healthy = False
        second = await server.dispatch(envelope)
        assert second == {"value": "warm"}

        # Once the cache is invalidated, the read hits the dead backend and fails.
        await server.cache.invalidate_tool("acme", "warm.read")
        with pytest.raises(UpstreamError):
            await server.dispatch(envelope)
