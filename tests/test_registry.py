"""Tests for tool registry and stub tool."""

from __future__ import annotations

import pytest
from pydantic import Field

from atlas_mcp.errors.framework import ToolNotFoundError
from atlas_mcp.tools.base import Tool, ToolLevel, ToolMetadata
from atlas_mcp.tools.registry import ToolRegistry
from atlas_mcp.tools.stub import StubPingTool
from atlas_mcp.validation.adversarial import StrictToolModel


class _ScopedInput(StrictToolModel):
    q: str = Field(default="x", max_length=32)


class _ScopedTool(Tool):
    meta = ToolMetadata(
        name="demo.scoped",
        description="Requires a read scope.",
        level=ToolLevel.ATOMIC,
        scopes_required=("tool:demo:read",),
        tags=("demo",),
    )
    input_schema = _ScopedInput

    async def run(self, tenant: str, args: _ScopedInput) -> dict:
        return {"tenant": tenant, "q": args.q}


@pytest.mark.asyncio
async def test_discover_registers_stub_ping() -> None:
    registry = ToolRegistry()
    await registry.discover()
    assert len(registry) == 1
    tool = registry.get("server.ping")
    assert tool.meta.name == "server.ping"
    assert "server.ping" in registry


@pytest.mark.asyncio
async def test_discover_is_idempotent() -> None:
    registry = ToolRegistry()
    await registry.discover()
    await registry.discover()
    assert len(registry) == 1


@pytest.mark.asyncio
async def test_stub_ping_run() -> None:
    tool = StubPingTool()
    args = tool.validate({"message": "hello"})
    result = await tool.run("acme", args)
    assert result == {
        "pong": True,
        "message": "hello",
        "tenant": "acme",
        "mode": "echo",
    }


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


@pytest.mark.asyncio
async def test_list_visible_hides_tools_without_required_scopes() -> None:
    registry = ToolRegistry()
    await registry.discover()
    registry.register(_ScopedTool())

    visible_without = registry.list_visible(tenant="acme", scopes=[])
    assert [t.name for t in visible_without] == ["server.ping"]

    visible_with = registry.list_visible(tenant="acme", scopes=["tool:demo:read"])
    assert {t.name for t in visible_with} == {"server.ping", "demo.scoped"}

    visible_admin = registry.list_visible(tenant="acme", scopes=["tool:*:admin"])
    assert {t.name for t in visible_admin} == {"server.ping", "demo.scoped"}


@pytest.mark.asyncio
async def test_capability_document_reflects_live_registrations() -> None:
    registry = ToolRegistry()
    await registry.discover()
    registry.register(_ScopedTool())

    doc = registry.capability_document()
    assert doc["capabilities"]["tools"]["count"] == 2
    assert "atomic" in doc["capabilities"]["tools"]["levels"]

    names = {entry["name"] for entry in doc["tools_summary"]}
    assert names == {"server.ping", "demo.scoped"}

    scoped = next(e for e in doc["tools_summary"] if e["name"] == "demo.scoped")
    assert scoped["scopes_required"] == ["tool:demo:read"]
    assert scoped["destructive"] is False


@pytest.mark.asyncio
async def test_unregister_updates_capability_document() -> None:
    registry = ToolRegistry()
    await registry.discover()
    registry.register(_ScopedTool())
    registry.unregister("demo.scoped")

    assert "demo.scoped" not in registry
    assert registry.capability_document()["capabilities"]["tools"]["count"] == 1
