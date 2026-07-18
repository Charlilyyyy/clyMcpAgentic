"""Atomic vector search tool (Qdrant-style HTTP API).

Exposes dense retrieval as a single atomic operation. The tool does NOT embed
the query — that is the caller's job (or the composed ``semantic_search``
tool). A mandatory tenant filter is merged into every request.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import Field

from atlas_mcp.config import get_settings
from atlas_mcp.errors.framework import UpstreamError
from atlas_mcp.tools.backends import VectorBackend
from atlas_mcp.tools.base import Tool, ToolLevel, ToolMetadata
from atlas_mcp.validation.adversarial import StrictToolModel


class VectorSearchInput(StrictToolModel):
    collection: str = Field(..., description="Vector collection name.", max_length=200)
    vector: list[float] = Field(..., min_length=1, description="Query embedding.")
    top_k: int = Field(10, ge=1, le=100)
    filter: dict[str, Any] | None = Field(default=None, description="Extra filter clauses.")


class VectorSearchTool(Tool):
    meta: ClassVar[ToolMetadata] = ToolMetadata(
        name="vector.search",
        description="Nearest-neighbour search against a vector collection.",
        level=ToolLevel.ATOMIC,
        scopes_required=("tool:vector:read",),
        cacheable=True,
        cache_ttl_seconds=300,
        timeout_ms=2_000,
        tags=("vector", "qdrant", "retrieval"),
    )
    input_schema: ClassVar[type[StrictToolModel]] = VectorSearchInput

    def __init__(self, backend: VectorBackend | None = None) -> None:
        self._backend = backend

    def _get_backend(self) -> VectorBackend:
        if self._backend is None:
            from atlas_mcp.tools.clients.vector_client import QdrantHttpBackend

            self._backend = QdrantHttpBackend(
                base_url=get_settings().vector_db_url,
                timeout_s=self.meta.timeout_ms / 1000,
            )
        return self._backend

    async def run(self, tenant: str, args: VectorSearchInput) -> dict:  # type: ignore[override]
        tenant_clause = {"key": "tenant", "match": {"value": tenant}}
        combined: dict[str, Any] = {"must": [tenant_clause]}
        if args.filter:
            combined["must"] = combined["must"] + list(args.filter.get("must") or [])
            if args.filter.get("should"):
                combined["should"] = args.filter["should"]
            if args.filter.get("must_not"):
                combined["must_not"] = args.filter["must_not"]

        try:
            matches = await self._get_backend().search(
                args.collection, args.vector, args.top_k, combined
            )
        except UpstreamError:
            raise
        except Exception as exc:
            raise UpstreamError(
                "vector_error",
                retryable=True,
                hint=str(exc)[:200] or "vector search failed",
            ) from exc

        return {
            "matches": [
                {"id": m.get("id"), "score": m.get("score"), "payload": m.get("payload") or {}}
                for m in matches
            ]
        }
