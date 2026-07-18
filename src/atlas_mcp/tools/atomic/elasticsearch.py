"""Atomic Elasticsearch search tool.

Runs a DSL query against an index with a mandatory tenant filter injected
server-side, so a tenant can never read another tenant's documents even if
the agent-supplied query omits the filter.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import Field

from atlas_mcp.config import get_settings
from atlas_mcp.errors.framework import UpstreamError
from atlas_mcp.tools.backends import ElasticsearchBackend
from atlas_mcp.tools.base import Tool, ToolLevel, ToolMetadata
from atlas_mcp.validation.adversarial import StrictToolModel


class ElasticsearchSearchInput(StrictToolModel):
    index: str = Field(..., description="Index or index pattern.", max_length=200)
    query: dict = Field(..., description="Elasticsearch DSL query body.")
    size: int = Field(20, ge=1, le=200)
    fields: list[str] | None = Field(default=None)


class ElasticsearchSearchTool(Tool):
    meta: ClassVar[ToolMetadata] = ToolMetadata(
        name="elasticsearch.search",
        description="Run a DSL search against an Elasticsearch index.",
        level=ToolLevel.ATOMIC,
        scopes_required=("tool:elasticsearch:read",),
        cacheable=True,
        cache_ttl_seconds=30,
        timeout_ms=3_000,
        tags=("elasticsearch", "search", "read"),
    )
    input_schema: ClassVar[type[StrictToolModel]] = ElasticsearchSearchInput

    def __init__(self, backend: ElasticsearchBackend | None = None) -> None:
        self._backend = backend

    def _get_backend(self) -> ElasticsearchBackend:
        if self._backend is None:
            from atlas_mcp.tools.clients.elasticsearch_client import ElasticsearchClientBackend

            self._backend = ElasticsearchClientBackend(url=get_settings().elasticsearch_url)
        return self._backend

    async def run(self, tenant: str, args: ElasticsearchSearchInput) -> dict:  # type: ignore[override]
        body: dict[str, Any] = {
            "query": {
                "bool": {
                    "must": [args.query],
                    "filter": [{"term": {"_tenant": tenant}}],
                }
            },
            "size": args.size,
        }
        if args.fields:
            body["_source"] = args.fields

        try:
            resp = await self._get_backend().search(tenant, args.index, body)
        except UpstreamError:
            raise
        except Exception as exc:
            status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
            raise UpstreamError(
                code="elasticsearch_error",
                retryable=status in (408, 429, 500, 502, 503, 504),
                hint=str(exc)[:200] or "elasticsearch query failed",
                context={"status": status},
            ) from exc

        hits = resp.get("hits", {}).get("hits", [])
        total = resp.get("hits", {}).get("total", {})
        return {
            "total": total.get("value") if isinstance(total, dict) else total,
            "took_ms": resp.get("took"),
            "hits": [
                {"id": h.get("_id"), "score": h.get("_score"), "source": h.get("_source", {})}
                for h in hits
            ],
        }
