"""Atomic S3 storage tools.

Reads and writes objects in a single bucket. Every object key is
tenant-prefixed so the bucket layout makes cross-tenant access physically
impossible, even if a policy bug lets a call through. Writes are destructive
and route through the approval gate by default.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import Field

from atlas_mcp.config import get_settings
from atlas_mcp.errors.framework import UpstreamError
from atlas_mcp.tools.backends import ObjectStoreBackend
from atlas_mcp.tools.base import Tool, ToolLevel, ToolMetadata
from atlas_mcp.validation.adversarial import StrictToolModel


def _tenant_key(tenant: str, key: str) -> str:
    return f"{tenant}/{key.lstrip('/')}"


def _default_backend() -> ObjectStoreBackend:
    from atlas_mcp.tools.clients.s3_client import Aioboto3Backend

    return Aioboto3Backend(endpoint_url=get_settings().s3_endpoint)


class S3GetInput(StrictToolModel):
    key: str = Field(..., description="Object key — prefixed with tenant/.", max_length=1024)
    max_bytes: int = Field(1_048_576, ge=1, le=10_485_760)


class S3GetTool(Tool):
    meta: ClassVar[ToolMetadata] = ToolMetadata(
        name="s3.get_object",
        description="Read a text object from the Atlas S3 bucket.",
        level=ToolLevel.ATOMIC,
        scopes_required=("tool:s3:read",),
        cacheable=True,
        cache_ttl_seconds=300,
        timeout_ms=3_000,
        tags=("s3", "storage", "read"),
    )
    input_schema: ClassVar[type[StrictToolModel]] = S3GetInput

    def __init__(self, backend: ObjectStoreBackend | None = None) -> None:
        self._backend = backend

    def _get_backend(self) -> ObjectStoreBackend:
        if self._backend is None:
            self._backend = _default_backend()
        return self._backend

    async def run(self, tenant: str, args: S3GetInput) -> dict:  # type: ignore[override]
        bucket = get_settings().s3_bucket
        try:
            body = await self._get_backend().get_object(
                bucket, _tenant_key(tenant, args.key), args.max_bytes
            )
        except UpstreamError:
            raise
        except Exception as exc:
            raise _map_s3_error(exc, key=args.key) from exc
        return {
            "key": args.key,
            "size_bytes": len(body),
            "content": body.decode("utf-8", errors="replace"),
        }


class S3PutInput(StrictToolModel):
    key: str = Field(..., description="Object key — prefixed with tenant/.", max_length=1024)
    content: str = Field(..., description="Text content to store.", max_length=1_000_000)
    content_type: str = Field("text/plain", max_length=128)


class S3PutTool(Tool):
    meta: ClassVar[ToolMetadata] = ToolMetadata(
        name="s3.put_object",
        description="Write a text object to the Atlas S3 bucket. Requires human approval.",
        level=ToolLevel.ATOMIC,
        scopes_required=("tool:s3:write",),
        destructive=True,
        cacheable=False,
        timeout_ms=5_000,
        tags=("s3", "storage", "write"),
    )
    input_schema: ClassVar[type[StrictToolModel]] = S3PutInput

    def __init__(self, backend: ObjectStoreBackend | None = None) -> None:
        self._backend = backend

    def _get_backend(self) -> ObjectStoreBackend:
        if self._backend is None:
            self._backend = _default_backend()
        return self._backend

    async def run(self, tenant: str, args: S3PutInput) -> dict:  # type: ignore[override]
        bucket = get_settings().s3_bucket
        payload = args.content.encode("utf-8")
        try:
            await self._get_backend().put_object(
                bucket,
                _tenant_key(tenant, args.key),
                payload,
                args.content_type,
                {"tenant": tenant},
            )
        except UpstreamError:
            raise
        except Exception as exc:
            raise _map_s3_error(exc, key=args.key) from exc
        return {"key": args.key, "bytes_written": len(payload)}


def _map_s3_error(exc: Exception, *, key: str) -> UpstreamError:
    code = None
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
    if code == "NoSuchKey":
        return UpstreamError("not_found", retryable=False, hint=f"s3 key {key!r} does not exist")
    return UpstreamError(
        "s3_error",
        retryable=code in ("InternalError", "SlowDown"),
        hint=str(exc)[:200] or "s3 operation failed",
        context={"s3_code": code},
    )
