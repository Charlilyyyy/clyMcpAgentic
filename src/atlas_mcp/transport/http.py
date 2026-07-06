"""Streamable HTTP transport and operational endpoints."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from atlas_mcp.auth.middleware import AuthMiddleware
from atlas_mcp.governance.tenant import TenantMiddleware
from atlas_mcp.transport.middleware import ContextMiddleware

if TYPE_CHECKING:
    from atlas_mcp.server import AtlasServer


async def healthz(_request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def build_readyz(server: "AtlasServer"):
    async def readyz(_request) -> JSONResponse:
        if len(server.registry) == 0:
            return JSONResponse({"status": "starting"}, status_code=503)
        return JSONResponse({"status": "ready"})

    return readyz


def build_http_app(server: "AtlasServer") -> Starlette:
    """Wrap the MCP server in a Starlette app with Streamable HTTP transport."""
    session_manager = StreamableHTTPSessionManager(
        app=server.mcp,
        stateless=server.settings.stateless_mode,
    )

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            await server.startup()
            try:
                yield
            finally:
                await server.shutdown()

    middleware = [
        Middleware(AuthMiddleware, settings=server.settings),
        Middleware(TenantMiddleware, settings=server.settings),
        Middleware(ContextMiddleware, settings=server.settings),
    ]

    routes = [
        Route("/.well-known/mcp-server", endpoint=server.registry.well_known_endpoint),
        Route("/healthz", endpoint=healthz),
        Route("/readyz", endpoint=build_readyz(server)),
        Mount("/mcp", app=session_manager.handle_request),
    ]
    return Starlette(routes=routes, middleware=middleware, lifespan=lifespan)
