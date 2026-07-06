"""Component 1 — Transport & Session Layer.

Atlas-MCP ships two transports from day one:

* stdio, for local development and single-user MCP hosts like Claude Desktop.
* Streamable HTTP, for remote deployments, multi-user access, and horizontal scaling.

The same tool registry and dispatch pipeline feed both transports.
"""

from __future__ import annotations

import logging

import uvicorn
from mcp.server import Server

from atlas_mcp.config import ServerSettings, get_settings
from atlas_mcp.context import current_context
from atlas_mcp.errors.framework import ToolError, to_call_tool_error
from atlas_mcp.tools.registry import ToolRegistry
from atlas_mcp.validation.schemas import ToolCallEnvelope

logger = logging.getLogger(__name__)


class AtlasServer:
    """Glue that holds the MCP server core and tool dispatch pipeline."""

    def __init__(self, settings: ServerSettings):
        self.settings = settings
        self.mcp = Server(settings.service_name)
        self.registry = ToolRegistry()
        self._register_mcp_handlers()

    def _register_mcp_handlers(self) -> None:
        @self.mcp.list_tools()
        async def list_tools():  # type: ignore[misc]
            ctx = current_context()
            return self.registry.list_visible(tenant=ctx.tenant, scopes=ctx.scopes)

        @self.mcp.call_tool(validate_input=False)
        async def call_tool(name: str, arguments: dict):  # type: ignore[misc]
            ctx = current_context()
            envelope = ctx.build_envelope(name, arguments)
            try:
                return await self.dispatch(envelope)
            except ToolError as exc:
                return to_call_tool_error(exc)

    async def dispatch(self, envelope: ToolCallEnvelope) -> dict:
        """Central request pipeline entry for tool calls.

        Auth, policy, rate limiting, cache, and circuit breaking are layered
        on in later commits. Today: validate arguments and execute the tool.
        """
        tool = self.registry.get(envelope.tool)
        validated_args = tool.validate(envelope.arguments)
        logger.info(
            "tool_dispatch",
            extra={
                "tool": envelope.tool,
                "tenant": envelope.tenant,
                "caller": envelope.caller,
            },
        )
        return await tool.execute(envelope.tenant, validated_args)

    async def startup(self) -> None:
        await self.registry.discover()
        logger.info("atlas-mcp ready", extra={"tools": len(self.registry)})

    async def shutdown(self) -> None:
        return None


def main() -> None:
    """CLI entry point for ``atlas-mcp``."""
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    server = AtlasServer(settings)

    if settings.transport == "stdio":
        raise SystemExit("stdio transport is wired in the next commit — set ATLAS_TRANSPORT=http")

    from atlas_mcp.transport.http import build_http_app

    app = build_http_app(server)
    uvicorn.run(app, host=settings.http_host, port=settings.http_port)


async def run_http(server: AtlasServer) -> None:
    """Programmatic entry for tests and embedding."""
    from atlas_mcp.transport.http import build_http_app

    config = uvicorn.Config(
        build_http_app(server),
        host=server.settings.http_host,
        port=server.settings.http_port,
        log_level=server.settings.log_level.lower(),
    )
    http_server = uvicorn.Server(config)
    await http_server.serve()


if __name__ == "__main__":
    main()
