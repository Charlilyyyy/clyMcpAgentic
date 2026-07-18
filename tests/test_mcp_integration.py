"""End-to-end MCP protocol integration tests."""

from __future__ import annotations

import os
import sys

import anyio
import httpx
import pytest
from httpx import ASGITransport
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from atlas_mcp.config import ServerSettings
from atlas_mcp.context import dev_context, reset_context, set_context
from atlas_mcp.server import AtlasServer
from atlas_mcp.transport.http import build_http_app


@pytest.mark.asyncio
async def test_in_process_list_and_call_stub_tool() -> None:
    """Exercises the same MCP run path used by stdio transport."""
    server = AtlasServer(ServerSettings())
    await server.startup()
    token = set_context(dev_context(tenant="acme"))

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
                names = [tool.name for tool in tools.tools]
                assert names == ["server.ping"]

                result = await session.call_tool("server.ping", {"message": "stdio-path"})
                assert not result.isError
                assert result.structuredContent == {
                    "pong": True,
                    "message": "stdio-path",
                    "tenant": "acme",
                    "mode": "echo",
                }
            tg.cancel_scope.cancel()
    finally:
        reset_context(token)
        await server.shutdown()


@pytest.mark.asyncio
async def test_http_list_and_call_stub_tool() -> None:
    server = AtlasServer(ServerSettings(auth_dev_token="test-token"))
    app = build_http_app(server)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            follow_redirects=True,
            headers={"Authorization": "Bearer test-token"},
        ) as http_client:
            async with streamable_http_client(
                "http://test/mcp/",
                http_client=http_client,
            ) as (read_stream, write_stream, _get_session_id):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    # Dev token carries tool:*:admin, so the full hierarchy is visible.
                    names = {tool.name for tool in tools.tools}
                    assert "server.ping" in names
                    assert "customer.build_context" in names

                    result = await session.call_tool(
                        "server.ping",
                        {"message": "http-path"},
                    )
                    assert not result.isError
                    assert result.structuredContent == {
                        "pong": True,
                        "message": "http-path",
                        "tenant": "acme",
                        "mode": "echo",
                    }


@pytest.mark.asyncio
async def test_stdio_subprocess_list_and_call_stub_tool() -> None:
    """Spawns ``python -m atlas_mcp.server`` with ATLAS_TRANSPORT=stdio."""
    env = os.environ.copy()
    env["ATLAS_TRANSPORT"] = "stdio"
    env["ATLAS_TENANT"] = "acme"
    env["PYTHONPATH"] = os.pathsep.join(
        [p for p in (os.environ.get("PYTHONPATH", ""), os.getcwd()) if p]
    )

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_mcp.server"],
        env=env,
    )

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert [tool.name for tool in tools.tools] == ["server.ping"]

            result = await session.call_tool("server.ping", {"message": "subprocess"})
            assert not result.isError
            assert result.structuredContent == {
                "pong": True,
                "message": "subprocess",
                "tenant": "acme",
                "mode": "echo",
            }
