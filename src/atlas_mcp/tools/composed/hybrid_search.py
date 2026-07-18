"""Composed hybrid search — Level 2.

Combines Elasticsearch BM25 lexical search with dense vector search and fuses
the two result sets using Reciprocal Rank Fusion (RRF). This pattern
consistently beats either retriever alone on enterprise knowledge bases that
mix technical jargon (BM25 wins) with natural-language queries (dense wins).
"""

from __future__ import annotations

import asyncio
from typing import ClassVar

from pydantic import Field

from atlas_mcp.tools.atomic.elasticsearch import ElasticsearchSearchInput, ElasticsearchSearchTool
from atlas_mcp.tools.atomic.embeddings import EmbeddingClient
from atlas_mcp.tools.atomic.vector_search import VectorSearchInput, VectorSearchTool
from atlas_mcp.tools.backends import Embedder
from atlas_mcp.tools.base import Tool, ToolLevel, ToolMetadata
from atlas_mcp.validation.adversarial import StrictToolModel


class HybridSearchInput(StrictToolModel):
    query: str = Field(..., min_length=1, max_length=4000)
    es_index: str = Field(..., description="Elasticsearch index pattern.", max_length=200)
    vector_collection: str = Field(..., description="Vector collection.", max_length=200)
    top_k: int = Field(10, ge=1, le=50)
    rrf_k: int = Field(60, ge=10, le=200)


class HybridSearchTool(Tool):
    meta: ClassVar[ToolMetadata] = ToolMetadata(
        name="hybrid_search",
        description=(
            "Hybrid lexical + semantic retrieval with Reciprocal Rank Fusion. "
            "Prefer this for queries that mix technical keywords with natural language."
        ),
        level=ToolLevel.COMPOSED,
        scopes_required=("tool:vector:read", "tool:elasticsearch:read"),
        cacheable=True,
        cache_ttl_seconds=120,
        timeout_ms=10_000,
        tags=("rag", "retrieval", "hybrid", "rrf"),
    )
    input_schema: ClassVar[type[StrictToolModel]] = HybridSearchInput

    def __init__(
        self,
        embedder: Embedder | None = None,
        es_tool: ElasticsearchSearchTool | None = None,
        vector_tool: VectorSearchTool | None = None,
    ) -> None:
        self.embedder = embedder or EmbeddingClient()
        self.es_tool = es_tool or ElasticsearchSearchTool()
        self.vector_tool = vector_tool or VectorSearchTool()

    async def run(self, tenant: str, args: HybridSearchInput) -> dict:  # type: ignore[override]
        es_task = asyncio.create_task(
            self.es_tool.run(
                tenant,
                ElasticsearchSearchInput(
                    index=args.es_index,
                    query={"multi_match": {"query": args.query, "fields": ["title^2", "body"]}},
                    size=args.top_k * 2,
                ),
            )
        )
        emb_task = asyncio.create_task(self.embedder.embed([args.query]))
        es_result, embeddings = await asyncio.gather(es_task, emb_task)

        vs_result = await self.vector_tool.run(
            tenant,
            VectorSearchInput(
                collection=args.vector_collection, vector=embeddings[0], top_k=args.top_k * 2
            ),
        )

        lexical_ranks = {h["id"]: rank for rank, h in enumerate(es_result["hits"], start=1)}
        dense_ranks = {str(m["id"]): rank for rank, m in enumerate(vs_result["matches"], start=1)}

        scored: list[tuple[str, float]] = []
        for doc_id in set(lexical_ranks) | set(dense_ranks):
            score = 0.0
            if doc_id in lexical_ranks:
                score += 1.0 / (args.rrf_k + lexical_ranks[doc_id])
            if doc_id in dense_ranks:
                score += 1.0 / (args.rrf_k + dense_ranks[doc_id])
            scored.append((doc_id, score))
        scored.sort(key=lambda x: x[1], reverse=True)

        by_id_es = {h["id"]: h for h in es_result["hits"]}
        by_id_vec = {str(m["id"]): m for m in vs_result["matches"]}

        results = []
        for doc_id, rrf_score in scored[: args.top_k]:
            es_hit = by_id_es.get(doc_id)
            vec_hit = by_id_vec.get(doc_id)
            results.append(
                {
                    "id": doc_id,
                    "rrf_score": round(rrf_score, 6),
                    "lexical_rank": lexical_ranks.get(doc_id),
                    "dense_rank": dense_ranks.get(doc_id),
                    "source": (es_hit or {}).get("source") or (vec_hit or {}).get("payload") or {},
                }
            )
        return {"query": args.query, "results": results, "fused_from": len(scored)}
