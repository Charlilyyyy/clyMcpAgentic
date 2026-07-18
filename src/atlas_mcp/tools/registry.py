"""Component 4 — Tool Registry & Discovery.

Two jobs:

1. Maintain an in-memory index of available tools so ``list_tools`` and
   ``call_tool`` can resolve by name without a disk hit per request.
2. Expose a ``/.well-known/mcp-server`` endpoint that lets registries,
   crawlers, and MCP hosts discover what this server offers *without
   connecting a session first*.

The discovery document is deliberately a *summary* — input schemas and
full policy details require an authenticated session.
"""

from __future__ import annotations

from typing import Iterable, Iterator

from mcp.types import Tool as MCPToolSpec
from starlette.requests import Request
from starlette.responses import JSONResponse

from atlas_mcp import __version__
from atlas_mcp.config import get_settings
from atlas_mcp.errors.framework import ToolNotFoundError
from atlas_mcp.tools.base import Tool, ToolLevel
from atlas_mcp.tools.stub import StubPingTool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._discovered = False

    def register(self, tool: Tool) -> None:
        """Register a tool. Raises if the name is already taken."""
        name = tool.meta.name
        if name in self._tools:
            raise ValueError(f"tool {name!r} already registered")
        self._tools[name] = tool

    def unregister(self, name: str) -> None:
        """Remove a tool by name. Raises :class:`ToolNotFoundError` if missing."""
        if name not in self._tools:
            raise ToolNotFoundError(name)
        del self._tools[name]

    async def discover(self) -> None:
        """Load built-in tools. Idempotent — a second call is a no-op.

        Loads the three-level hierarchy: atomic → composed → workflow. In
        production these would come from setuptools entry points so third
        parties can plug in tools by installing a package.
        """
        if self._discovered:
            return

        from atlas_mcp.tools.atomic.elasticsearch import ElasticsearchSearchTool
        from atlas_mcp.tools.atomic.http_client import HTTPFetchTool
        from atlas_mcp.tools.atomic.postgres import PostgresQueryTool
        from atlas_mcp.tools.atomic.s3_storage import S3GetTool, S3PutTool
        from atlas_mcp.tools.atomic.vector_search import VectorSearchTool
        from atlas_mcp.tools.composed.hybrid_search import HybridSearchTool
        from atlas_mcp.tools.composed.semantic_search import SemanticSearchTool
        from atlas_mcp.tools.workflow.customer_context import CustomerContextTool

        self.register(StubPingTool())
        for cls in (
            PostgresQueryTool,
            ElasticsearchSearchTool,
            VectorSearchTool,
            S3GetTool,
            S3PutTool,
            HTTPFetchTool,
        ):
            self.register(cls())
        for cls in (SemanticSearchTool, HybridSearchTool):
            self.register(cls())
        self.register(CustomerContextTool())

        self._discovered = True

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFoundError(name) from None

    def names(self) -> list[str]:
        return sorted(self._tools)

    def by_level(self, level: ToolLevel) -> list[Tool]:
        return [tool for tool in self._tools.values() if tool.meta.level == level]

    def capability_document(self) -> dict:
        """Build the ``/.well-known/mcp-server`` payload from live registrations."""
        settings = get_settings()
        return {
            "protocol_version": "2025-11",
            "server": {"name": settings.service_name, "version": __version__},
            "capabilities": {
                "tools": {
                    "list_changed": True,
                    "count": len(self._tools),
                    "levels": sorted({t.meta.level.value for t in self._tools.values()}),
                },
                "prompts": {},
                "resources": {},
            },
            "tools_summary": [self._tool_summary(tool) for tool in self._tools.values()],
            "authorization_server": {
                "issuer": settings.auth_issuer,
                "metadata_url": (
                    f"{settings.auth_issuer}/.well-known/oauth-authorization-server"
                ),
            },
        }

    def list_visible(self, tenant: str, scopes: Iterable[str]) -> list[MCPToolSpec]:
        """Return MCP tool specs the caller is allowed to see.

        This is a list-time filter, not call-time authorization — policy
        still runs on every invocation. Hiding unusable tools keeps the
        agent's context window clean.
        """
        _ = tenant
        scope_set = set(scopes)
        visible: list[MCPToolSpec] = []
        for tool in self._tools.values():
            if not self._scopes_satisfied(tool.meta.scopes_required, scope_set):
                continue
            visible.append(_to_mcp_spec(tool))
        return visible

    async def well_known_endpoint(self, _request: Request) -> JSONResponse:
        return JSONResponse(self.capability_document())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._tools

    def __iter__(self) -> Iterator[Tool]:
        return iter(self._tools.values())

    @staticmethod
    def _scopes_satisfied(required: tuple[str, ...], held: set[str]) -> bool:
        if not required:
            return True
        return all(scope in held or "tool:*:admin" in held for scope in required)

    @staticmethod
    def _tool_summary(tool: Tool) -> dict:
        return {
            "name": tool.meta.name,
            "level": tool.meta.level.value,
            "description": tool.meta.description,
            "tags": list(tool.meta.tags),
            "scopes_required": list(tool.meta.scopes_required),
            "destructive": tool.meta.destructive,
            "cacheable": tool.meta.cacheable,
        }


def _to_mcp_spec(tool: Tool) -> MCPToolSpec:
    return MCPToolSpec(
        name=tool.meta.name,
        description=tool.meta.description,
        inputSchema=tool.input_schema.model_json_schema(),
    )
