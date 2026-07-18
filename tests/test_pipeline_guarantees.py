"""Phase exit criteria — validation order, SERF wire format, dynamic registry."""

from __future__ import annotations

import anyio
import pytest
from mcp import ClientSession
from pydantic import Field

from atlas_mcp.auth.policy import PolicyEngine, Rule
from atlas_mcp.config import ServerSettings
from atlas_mcp.context import dev_context, reset_context, set_context
from atlas_mcp.errors.framework import PolicyError, ValidationError
from atlas_mcp.server import AtlasServer
from atlas_mcp.tools.base import Tool, ToolLevel, ToolMetadata
from atlas_mcp.validation.adversarial import StrictToolModel
from atlas_mcp.validation.schemas import ToolCallEnvelope


class _EchoInput(StrictToolModel):
    message: str = Field(default="hi", max_length=20)


class _EchoTool(Tool):
    meta = ToolMetadata(
        name="demo.echo",
        description="Echo for dynamic-registration tests.",
        level=ToolLevel.ATOMIC,
        scopes_required=("tool:demo:read",),
        tags=("demo", "echo"),
        cacheable=False,
    )
    input_schema = _EchoInput

    async def run(self, tenant: str, args: _EchoInput) -> dict:
        return {"echo": args.message, "tenant": tenant}


class _TrackingPolicy(PolicyEngine):
    """Policy engine that records whether ``check`` was invoked."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.check_calls = 0

    def check(self, subject: str, tenant: str, action: str, resource: str, context: dict) -> None:
        self.check_calls += 1
        super().check(subject, tenant, action, resource, context)


@pytest.fixture
async def server() -> AtlasServer:
    instance = AtlasServer(ServerSettings())
    await instance.startup()
    return instance


@pytest.mark.asyncio
async def test_invalid_payload_rejected_before_policy(server: AtlasServer) -> None:
    """Invalid arguments must fail at validation — policy must never run."""
    tracking = _TrackingPolicy(
        rules=[
            Rule(
                id="allow-ping",
                subjects=("*",),
                actions=("server.ping",),
                resources=("*",),
            )
        ]
    )
    server.policy = tracking

    envelope = ToolCallEnvelope(
        tool="server.ping",
        arguments={"message": "x" * 300, "evil_extra": True},
        tenant="acme",
        caller="dev:local",
    )
    with pytest.raises(ValidationError) as exc_info:
        await server.dispatch(envelope)

    assert exc_info.value.code == "invalid_arguments"
    assert tracking.check_calls == 0


@pytest.mark.asyncio
async def test_unknown_fields_rejected_before_policy(server: AtlasServer) -> None:
    tracking = _TrackingPolicy(
        rules=[Rule(id="allow-ping", subjects=("*",), actions=("server.ping",), resources=("*",))]
    )
    server.policy = tracking

    with pytest.raises(ValidationError):
        await server.dispatch(
            ToolCallEnvelope(
                tool="server.ping",
                arguments={"message": "ok", "not_in_schema": 1},
                tenant="acme",
                caller="dev:local",
            )
        )
    assert tracking.check_calls == 0


@pytest.mark.asyncio
async def test_valid_call_reaches_policy_then_executes(server: AtlasServer) -> None:
    tracking = _TrackingPolicy(
        rules=[Rule(id="allow-ping", subjects=("*",), actions=("server.ping",), resources=("*",))]
    )
    server.policy = tracking
    result = await server.dispatch(
        ToolCallEnvelope(
            tool="server.ping",
            arguments={"message": "ok"},
            tenant="acme",
            caller="dev:local",
        )
    )
    assert tracking.check_calls == 1
    assert result["pong"] is True


@pytest.mark.asyncio
async def test_tool_list_reflects_dynamic_registration(server: AtlasServer) -> None:
    before = {
        t.name for t in server.registry.list_visible(tenant="acme", scopes=["tool:demo:read"])
    }
    assert "demo.echo" not in before

    server.registry.register(_EchoTool())

    after = {t.name for t in server.registry.list_visible(tenant="acme", scopes=["tool:demo:read"])}
    assert after == before | {"demo.echo"}

    doc = server.registry.capability_document()
    assert doc["capabilities"]["tools"]["count"] == len(server.registry)
    assert any(entry["name"] == "demo.echo" for entry in doc["tools_summary"])

    # Unregistering removes it from both list_tools and discovery summary.
    server.registry.unregister("demo.echo")
    assert "demo.echo" not in {
        t.name for t in server.registry.list_visible(tenant="acme", scopes=["tool:demo:read"])
    }
    assert not any(
        e["name"] == "demo.echo" for e in server.registry.capability_document()["tools_summary"]
    )


@pytest.mark.asyncio
async def test_mcp_call_tool_returns_json_parseable_serf_error(server: AtlasServer) -> None:
    """MCP wire path: validation failures arrive as structured CallToolResult errors."""
    token = set_context(dev_context(tenant="acme", scopes=("tool:*:admin",)))
    client_send, server_recv = anyio.create_memory_object_stream(10)
    server_send, client_recv = anyio.create_memory_object_stream(10)
    init_options = server.mcp.create_initialization_options()

    async def run_server() -> None:
        await server.mcp.run(
            server_recv,
            server_send,
            init_options,
            stateless=server.settings.stateless_mode,
        )

    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(run_server)
            async with ClientSession(client_recv, client_send) as session:
                await session.initialize()
                result = await session.call_tool(
                    "server.ping",
                    {"message": "x" * 300},
                )
                assert result.isError is True
                payload = result.structuredContent
                assert payload is not None
                assert payload["code"] == "invalid_arguments"
                assert payload["retryable"] is False
                assert isinstance(payload["hint"], str) and payload["hint"]
                assert "tool" in payload["context"]
            tg.cancel_scope.cancel()
    finally:
        reset_context(token)


@pytest.mark.asyncio
async def test_mcp_list_tools_sees_newly_registered_tool(server: AtlasServer) -> None:
    server.registry.register(_EchoTool())
    server.policy = PolicyEngine(
        rules=[
            Rule(id="allow-echo", subjects=("*",), actions=("demo.echo",), resources=("*",)),
            Rule(id="allow-ping", subjects=("*",), actions=("server.ping",), resources=("*",)),
        ]
    )

    token = set_context(dev_context(tenant="acme", scopes=("tool:demo:read", "tool:*:admin")))
    client_send, server_recv = anyio.create_memory_object_stream(10)
    server_send, client_recv = anyio.create_memory_object_stream(10)
    init_options = server.mcp.create_initialization_options()

    async def run_server() -> None:
        await server.mcp.run(
            server_recv,
            server_send,
            init_options,
            stateless=server.settings.stateless_mode,
        )

    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(run_server)
            async with ClientSession(client_recv, client_send) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert "server.ping" in names
                assert "demo.echo" in names

                # Input schema from list_tools includes the hardened constraint surface.
                echo = next(t for t in tools.tools if t.name == "demo.echo")
                assert echo.inputSchema["properties"]["message"]["maxLength"] == 20
            tg.cancel_scope.cancel()
    finally:
        reset_context(token)


@pytest.mark.asyncio
async def test_policy_still_enforced_after_valid_input(server: AtlasServer) -> None:
    server.policy = PolicyEngine(rules=[], default_deny=True)
    with pytest.raises(PolicyError) as exc_info:
        await server.dispatch(
            ToolCallEnvelope(
                tool="server.ping",
                arguments={"message": "ok"},
                tenant="acme",
                caller="dev:local",
            )
        )
    assert exc_info.value.code == "not_authorized"
