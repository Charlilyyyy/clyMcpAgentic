"""Atomic HTTP fetch tool — controlled outbound HTTP.

Refuses to hit any host not on the per-tenant allowlist. This is the primary
defence against data exfiltration after a successful prompt injection. The
dispatch pipeline also enforces the allowlist centrally; the tool repeats the
check as defence-in-depth.
"""

from __future__ import annotations

from typing import Any, ClassVar
from urllib.parse import urlparse

import httpx
from pydantic import Field, field_validator

from atlas_mcp.config import get_settings
from atlas_mcp.errors.framework import UpstreamError
from atlas_mcp.governance.http_allowlist import HttpAllowlist
from atlas_mcp.tools.base import Tool, ToolLevel, ToolMetadata
from atlas_mcp.validation.adversarial import StrictToolModel

_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


class HTTPFetchInput(StrictToolModel):
    url: str = Field(..., description="Fully-qualified https:// URL.", max_length=2048)
    method: str = Field("GET", description="GET, POST, PUT, PATCH, DELETE.")
    headers: dict[str, str] | None = Field(default=None)
    body: dict | None = Field(default=None, description="JSON body for write methods.")
    timeout_s: float = Field(10.0, ge=0.5, le=30.0)

    @field_validator("url")
    @classmethod
    def must_be_https(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https":
            raise ValueError("only https:// URLs are permitted")
        if not parsed.hostname:
            raise ValueError("missing hostname")
        return value

    @field_validator("method")
    @classmethod
    def method_allowed(cls, value: str) -> str:
        upper = value.upper()
        if upper not in _ALLOWED_METHODS:
            raise ValueError("unsupported method")
        return upper


class HTTPFetchTool(Tool):
    meta: ClassVar[ToolMetadata] = ToolMetadata(
        name="http.fetch",
        description="Make an outbound HTTPS request to an allowlisted domain.",
        level=ToolLevel.ATOMIC,
        scopes_required=("tool:http:fetch",),
        destructive=False,
        cacheable=False,
        timeout_ms=15_000,
        tags=("http", "network"),
    )
    input_schema: ClassVar[type[StrictToolModel]] = HTTPFetchInput

    def __init__(self, allowlist: HttpAllowlist | None = None, transport: Any = None) -> None:
        self._allowlist = allowlist
        self._transport = transport  # injectable httpx transport for tests

    def _get_allowlist(self) -> HttpAllowlist:
        if self._allowlist is None:
            self._allowlist = HttpAllowlist.from_file(get_settings().http_allowlist_file)
        return self._allowlist

    async def run(self, tenant: str, args: HTTPFetchInput) -> dict:  # type: ignore[override]
        host = urlparse(args.url).hostname or ""
        self._get_allowlist().check(tenant, host)

        try:
            async with httpx.AsyncClient(
                timeout=args.timeout_s, follow_redirects=False, transport=self._transport
            ) as client:
                resp = await client.request(
                    args.method, args.url, headers=args.headers or {}, json=args.body
                )
        except httpx.TimeoutException as exc:
            raise UpstreamError("http_timeout", retryable=True, hint=str(exc)) from exc
        except httpx.RequestError as exc:
            raise UpstreamError("http_network_error", retryable=True, hint=str(exc)) from exc

        body_bytes = resp.content[: 64 * 1024]
        return {
            "status": resp.status_code,
            "headers": dict(resp.headers),
            "body": body_bytes.decode("utf-8", errors="replace"),
            "truncated": len(resp.content) > len(body_bytes),
        }
