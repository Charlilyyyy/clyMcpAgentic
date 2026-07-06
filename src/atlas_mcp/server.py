"""Component 1 — Transport & Session Layer.

Atlas-MCP ships two transports from day one:

* stdio, for local development and single-user MCP hosts like Claude Desktop.
* Streamable HTTP, for remote deployments, multi-user access, and horizontal scaling.

The same tool registry and dispatch pipeline feed both transports. HTTP and
stdio wiring are added in subsequent commits; this module owns the MCP
``Server`` instance, handler registration, and the central ``dispatch`` path.
"""

from __future__ import annotations

import logging

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
    """CLI entry point for ``atlas-mcp``.

    Transport selection (stdio vs HTTP) is wired in a later commit.
    """
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    server = AtlasServer(settings)
    logger.info(
        "atlas-mcp core ready — transport wiring pending",
        extra={"transport": settings.transport, "tools": len(server.registry)},
    )


if __name__ == "__main__":
    main()
