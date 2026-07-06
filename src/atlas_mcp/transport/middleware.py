"""Middleware that binds request context before MCP handlers run."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from atlas_mcp.auth.oauth import Principal
from atlas_mcp.config import ServerSettings
from atlas_mcp.context import RequestContext, dev_context, reset_context, set_context

_PUBLIC_PATHS = frozenset({"/.well-known/mcp-server", "/healthz", "/readyz", "/metrics"})


class ContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: ServerSettings):
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        principal: Principal | None = getattr(request.state, "principal", None)
        if principal is not None:
            ctx = RequestContext(
                subject=principal.subject,
                tenant=principal.tenant,
                scopes=tuple(principal.scopes),
                delegator=principal.delegator,
            )
        else:
            tenant = request.headers.get(self.settings.tenant_header, "default")
            ctx = dev_context(tenant=tenant)

        token = set_context(ctx)
        try:
            return await call_next(request)
        finally:
            reset_context(token)
