"""Tests for AtlasServer dispatch pipeline."""

from __future__ import annotations

import pytest

from atlas_mcp.auth.policy import PolicyEngine, Rule
from atlas_mcp.config import ServerSettings
from atlas_mcp.errors.framework import PolicyError, ToolNotFoundError, ValidationError
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
    assert result == {
        "pong": True,
        "message": "hello",
        "tenant": "acme",
        "mode": "echo",
    }


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


@pytest.mark.asyncio
async def test_dispatch_denied_outside_tenant_scope(server: AtlasServer) -> None:
    server.policy = PolicyEngine(
        rules=[
            Rule(
                id="acme-only",
                subjects=("*",),
                actions=("server.ping",),
                resources=("tenant:acme/*",),
            )
        ]
    )
    envelope = ToolCallEnvelope(
        tool="server.ping",
        arguments={"message": "hello"},
        tenant="globex",
        caller="dev:local",
    )
    with pytest.raises(PolicyError) as exc_info:
        await server.dispatch(envelope)
    assert exc_info.value.code == "not_authorized"


@pytest.mark.asyncio
async def test_dispatch_denied_when_default_deny(server: AtlasServer) -> None:
    server.policy = PolicyEngine(rules=[], default_deny=True)
    envelope = ToolCallEnvelope(
        tool="server.ping",
        arguments={"message": "hello"},
        tenant="acme",
        caller="dev:local",
    )
    with pytest.raises(PolicyError):
        await server.dispatch(envelope)
