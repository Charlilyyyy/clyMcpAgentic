"""stdio transport for local MCP hosts (Claude Desktop, Cursor)."""

from __future__ import annotations

import os

from mcp.server.stdio import stdio_server

from atlas_mcp.context import dev_context, reset_context, set_context
from atlas_mcp.server import AtlasServer


async def run_stdio(server: AtlasServer) -> None:
    """Run the MCP server over process stdin/stdout."""
    tenant = os.environ.get("ATLAS_TENANT", "default")
    token = set_context(dev_context(tenant=tenant))
    await server.startup()
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.mcp.run(
                read_stream,
                write_stream,
                server.mcp.create_initialization_options(),
                stateless=server.settings.stateless_mode,
            )
    finally:
        await server.shutdown()
        reset_context(token)
