"""Tests for AtlasServer dispatch pipeline."""

from __future__ import annotations

import pytest

from atlas_mcp.config import ServerSettings
from atlas_mcp.errors.framework import ToolNotFoundError, ValidationError
from atlas_mcp.server import AtlasServer
from atlas_mcp.validation.schemas import ToolCallEnvelope


@pytest.fixture
async def server() -> AtlasServer:
    instance = AtlasServer(ServerSettings())
    await instance.startup()
    return instance


@pytest.mark.asyncio
async def test_startup_registers_stub_tool(server: AtlasServer) -> None:
    assert len(server.registry) == 1


@pytest.mark.asyncio
async def test_dispatch_ping(server: AtlasServer) -> None:
    envelope = ToolCallEnvelope(
        tool="server.ping",
        arguments={"message": "hello"},
        tenant="acme",
        caller="dev:local",
    )
    result = await server.dispatch(envelope)
    assert result == {"pong": True, "message": "hello", "tenant": "acme"}


@pytest.mark.asyncio
async def test_dispatch_unknown_tool(server: AtlasServer) -> None:
    envelope = ToolCallEnvelope(
        tool="missing.tool",
        arguments={},
        tenant="acme",
        caller="dev:local",
    )
    with pytest.raises(ToolNotFoundError):
        await server.dispatch(envelope)


@pytest.mark.asyncio
async def test_dispatch_invalid_arguments(server: AtlasServer) -> None:
    envelope = ToolCallEnvelope(
        tool="server.ping",
        arguments={"message": "x" * 300},
        tenant="acme",
        caller="dev:local",
    )
    with pytest.raises(ValidationError):
        await server.dispatch(envelope)
