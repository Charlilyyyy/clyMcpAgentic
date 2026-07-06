"""Tests for tool registry and stub tool."""

from __future__ import annotations

import pytest

from atlas_mcp.errors.framework import ToolNotFoundError
from atlas_mcp.tools.registry import ToolRegistry
from atlas_mcp.tools.stub import StubPingTool


@pytest.mark.asyncio
async def test_discover_registers_stub_ping() -> None:
    registry = ToolRegistry()
    await registry.discover()
    assert len(registry) == 1
    tool = registry.get("server.ping")
    assert tool.meta.name == "server.ping"


@pytest.mark.asyncio
async def test_stub_ping_run() -> None:
    tool = StubPingTool()
    args = tool.validate({"message": "hello"})
    result = await tool.run("acme", args)
    assert result == {"pong": True, "message": "hello", "tenant": "acme"}


def test_get_unknown_tool_raises() -> None:
    registry = ToolRegistry()
    with pytest.raises(ToolNotFoundError):
        registry.get("missing.tool")


@pytest.mark.asyncio
async def test_list_visible_includes_stub() -> None:
    registry = ToolRegistry()
    await registry.discover()
    tools = registry.list_visible(tenant="acme", scopes=[])
    assert len(tools) == 1
    assert tools[0].name == "server.ping"
