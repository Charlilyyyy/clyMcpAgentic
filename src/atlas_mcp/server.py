"""Component 1 — Transport & Session Layer.

Atlas-MCP ships two transports from day one:

* stdio, for local development and single-user MCP hosts like Claude Desktop.
* Streamable HTTP, for remote deployments, multi-user access, and horizontal scaling.

The same tool registry and dispatch pipeline feed both transports.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from urllib.parse import urlparse

import uvicorn
from mcp.server import Server
from pydantic import BaseModel

from atlas_mcp.auth.policy import PolicyEngine
from atlas_mcp.config import ServerSettings, get_settings
from atlas_mcp.context import current_context
from atlas_mcp.errors.framework import PolicyError, ToolError, as_call_tool_result, normalise_exception
from atlas_mcp.governance.http_allowlist import HttpAllowlist
from atlas_mcp.tools.base import Tool
from atlas_mcp.tools.registry import ToolRegistry
from atlas_mcp.validation.schemas import ToolCallEnvelope

logger = logging.getLogger(__name__)


class AtlasServer:
    """Glue that holds the MCP server core and tool dispatch pipeline."""

    def __init__(self, settings: ServerSettings):
        self.settings = settings
        self.mcp = Server(settings.service_name)
        self.registry = ToolRegistry()
        self.policy = PolicyEngine.from_file(
            Path(settings.policy_file),
            default_deny=settings.policy_default_deny,
        )
        self.http_allowlist = HttpAllowlist.from_file(settings.http_allowlist_file)
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
            except Exception as exc:
                # Never leak Python tracebacks to MCP clients — SERF only.
                if not isinstance(exc, ToolError):
                    logger.exception(
                        "unhandled_tool_error",
                        extra={"tool": name, "tenant": envelope.tenant},
                    )
                return as_call_tool_result(exc, tool=name)

    async def dispatch(self, envelope: ToolCallEnvelope) -> dict:
        """Central request pipeline entry for tool calls."""
        tool = self.registry.get(envelope.tool)
        validated_args = tool.validate(envelope.arguments)

        ctx = current_context()
        for required in tool.meta.scopes_required:
            if not ctx.has_scope(required):
                raise PolicyError(
                    "insufficient_scope",
                    retryable=False,
                    hint=f"missing required scope {required!r}",
                )

        self.policy.check(
            subject=envelope.caller,
            tenant=envelope.tenant,
            action=_policy_action(tool),
            resource=_policy_resource(tool, validated_args),
            context=_policy_context(validated_args),
        )
        _enforce_http_allowlist(self.http_allowlist, envelope.tenant, validated_args)

        logger.info(
            "tool_dispatch",
            extra={
                "tool": envelope.tool,
                "tenant": envelope.tenant,
                "caller": envelope.caller,
            },
        )
        try:
            return await tool.execute(envelope.tenant, validated_args)
        except ToolError:
            raise
        except Exception as exc:
            logger.exception(
                "tool_execution_failed",
                extra={"tool": envelope.tool, "tenant": envelope.tenant},
            )
            raise normalise_exception(exc, tool=envelope.tool) from exc

    async def startup(self) -> None:
        await self.registry.discover()
        logger.info("atlas-mcp ready", extra={"tools": len(self.registry)})

    async def shutdown(self) -> None:
        return None


def _policy_action(tool: Tool) -> str:
    return tool.meta.name


def _policy_resource(tool: Tool, args: BaseModel) -> str:
    _ = args
    return tool.meta.name


def _policy_context(args: BaseModel) -> dict:
    data = args.model_dump()
    context: dict = {}
    if "sql" in data:
        context["sql"] = data["sql"]
    if "columns" in data:
        context["columns"] = data["columns"]
    return context


def _enforce_http_allowlist(allowlist: HttpAllowlist, tenant: str, args: BaseModel) -> None:
    url = args.model_dump().get("url")
    if not url:
        return
    host = urlparse(str(url)).hostname or ""
    allowlist.check(tenant, host)


def main() -> None:
    """CLI entry point for ``atlas-mcp``."""
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    server = AtlasServer(settings)

    if settings.transport == "stdio":
        from atlas_mcp.transport.stdio import run_stdio

        asyncio.run(run_stdio(server))
        return

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
