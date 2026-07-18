"""Rate limiting + caching wired into the dispatch pipeline."""

from __future__ import annotations

import contextlib
from typing import ClassVar

import pytest

from atlas_mcp.config import ServerSettings
from atlas_mcp.context import RequestContext, dev_context, reset_context, set_context
from atlas_mcp.errors.framework import PolicyError, RateLimitError
from atlas_mcp.server import AtlasServer
from atlas_mcp.tools.base import Tool, ToolLevel, ToolMetadata
from atlas_mcp.validation.adversarial import StrictToolModel


class _Args(StrictToolModel):
    pass


class _CountingTool(Tool):
    meta: ClassVar[ToolMetadata] = ToolMetadata(
        name="count.read",
        description="Counts executions to reveal cache hits.",
        level=ToolLevel.ATOMIC,
        scopes_required=(),
        destructive=False,
        cacheable=True,
        cache_ttl_seconds=60,
    )
    input_schema: ClassVar[type[StrictToolModel]] = _Args

    def __init__(self) -> None:
        self.executions = 0

    async def run(self, tenant, args):  # type: ignore[override]
        self.executions += 1
        return {"executions": self.executions}


def _server(tool: Tool, *, allow_policy: bool = True, **settings_kwargs) -> AtlasServer:
    server = AtlasServer(ServerSettings(**settings_kwargs))
    server.registry.register(tool)
    if allow_policy:
        server.policy.check = lambda **kwargs: None  # type: ignore[method-assign]
    else:
        def _deny(**kwargs):
            raise PolicyError("denied", retryable=False, hint="no")

        server.policy.check = _deny  # type: ignore[method-assign]
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
async def test_repeated_identical_calls_served_from_cache() -> None:
    tool = _CountingTool()
    server = _server(tool)
    with _active_context():
        envelope = _ctx().build_envelope("count.read", {})
        first = await server.dispatch(envelope)
        second = await server.dispatch(envelope)
    assert first == second == {"executions": 1}
    assert tool.executions == 1  # second call was a cache hit
    assert server.cache.stats()["hits"] >= 1


@pytest.mark.asyncio
async def test_burst_is_throttled_per_tenant() -> None:
    tool = _CountingTool()
    # Disable cache so every call reaches the limiter with a fresh execution.
    server = _server(tool, cache_enabled=False, rate_limit_burst=3, rate_limit_default_rpm=1)
    with _active_context():
        envelope = _ctx().build_envelope("count.read", {})
        for _ in range(3):
            await server.dispatch(envelope)
        with pytest.raises(RateLimitError):
            await server.dispatch(envelope)


@pytest.mark.asyncio
async def test_policy_denied_calls_do_not_consume_quota() -> None:
    tool = _CountingTool()
    server = _server(tool, allow_policy=False, rate_limit_burst=2, rate_limit_default_rpm=1)
    with _active_context():
        envelope = _ctx().build_envelope("count.read", {})
        # Many denied calls...
        for _ in range(10):
            with pytest.raises(PolicyError):
                await server.dispatch(envelope)
        # ...leave the bucket untouched: allow policy through and confirm quota intact.
        server.policy.check = lambda **kwargs: None  # type: ignore[method-assign]
        for _ in range(2):
            await server.dispatch(envelope)
