"""Multi-tenancy boundary enforced at the HTTP middleware layer."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from atlas_mcp.config import ServerSettings

_EXEMPT_PATHS = frozenset({"/.well-known/mcp-server", "/healthz", "/readyz", "/metrics"})


class TenantMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: ServerSettings):
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        principal = getattr(request.state, "principal", None)
        if principal is None and self.settings.require_tenant:
            return JSONResponse({"error": "no_principal"}, status_code=401)

        tenant = principal.tenant if principal is not None else "default"

        header_tenant = request.headers.get(self.settings.tenant_header)
        if header_tenant and header_tenant != tenant:
            if principal is None or not principal.has_scope("tenant:*:impersonate"):
                return JSONResponse({"error": "tenant_mismatch"}, status_code=403)
            tenant = header_tenant

        request.state.tenant = tenant
        return await call_next(request)
