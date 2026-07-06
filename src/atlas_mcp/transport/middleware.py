"""Middleware that binds request context before MCP handlers run.

Auth middleware replaces the dev defaults in a later commit. Until then,
tenant is read from ``X-Tenant-Id`` so tool calls are tenant-scoped.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from atlas_mcp.config import ServerSettings
from atlas_mcp.context import dev_context, reset_context, set_context

_PUBLIC_PATHS = frozenset({"/.well-known/mcp-server", "/healthz", "/readyz"})


class ContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: ServerSettings):
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        tenant = request.headers.get(self.settings.tenant_header, "default")
        token = set_context(dev_context(tenant=tenant))
        try:
            return await call_next(request)
        finally:
            reset_context(token)
