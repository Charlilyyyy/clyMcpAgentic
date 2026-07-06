"""Tool registry and discovery endpoint."""

from __future__ import annotations

from typing import Iterable

from mcp.types import Tool as MCPToolSpec
from starlette.requests import Request
from starlette.responses import JSONResponse

from atlas_mcp import __version__
from atlas_mcp.config import get_settings
from atlas_mcp.errors.framework import ToolNotFoundError
from atlas_mcp.tools.base import Tool
from atlas_mcp.tools.stub import StubPingTool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        name = tool.meta.name
        if name in self._tools:
            raise ValueError(f"tool {name!r} already registered")
        self._tools[name] = tool

    async def discover(self) -> None:
        """Load built-in tools. Third-party entry points can extend this later."""
        self.register(StubPingTool())

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFoundError(name) from None

    def __len__(self) -> int:
        return len(self._tools)

    def list_visible(self, tenant: str, scopes: Iterable[str]) -> list[MCPToolSpec]:
        """Return MCP tool specs visible to the caller."""
        _ = tenant  # tenant-scoped filtering lands with auth/policy wiring
        scope_set = set(scopes)
        visible: list[MCPToolSpec] = []
        for tool in self._tools.values():
            if tool.meta.scopes_required and not all(
                scope in scope_set or "tool:*:admin" in scope_set
                for scope in tool.meta.scopes_required
            ):
                continue
            visible.append(_to_mcp_spec(tool))
        return visible

    async def well_known_endpoint(self, _request: Request) -> JSONResponse:
        settings = get_settings()
        return JSONResponse(
            {
                "protocol_version": "2025-11",
                "server": {"name": settings.service_name, "version": __version__},
                "capabilities": {
                    "tools": {"list_changed": True},
                    "prompts": {},
                    "resources": {},
                },
                "tools_summary": [
                    {
                        "name": tool.meta.name,
                        "level": tool.meta.level.value,
                        "description": tool.meta.description,
                        "tags": list(tool.meta.tags),
                    }
                    for tool in self._tools.values()
                ],
                "authorization_server": {
                    "issuer": settings.auth_issuer,
                    "metadata_url": (
                        f"{settings.auth_issuer}/.well-known/oauth-authorization-server"
                    ),
                },
            }
        )


def _to_mcp_spec(tool: Tool) -> MCPToolSpec:
    return MCPToolSpec(
        name=tool.meta.name,
        description=tool.meta.description,
        inputSchema=tool.input_schema.model_json_schema(),
    )
