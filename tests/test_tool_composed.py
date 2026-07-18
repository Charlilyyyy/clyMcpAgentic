"""Composed tools — semantic search hydration and hybrid RRF fusion."""

from __future__ import annotations

import pytest

from atlas_mcp.tools.atomic.elasticsearch import ElasticsearchSearchTool
from atlas_mcp.tools.atomic.postgres import PostgresQueryTool
from atlas_mcp.tools.atomic.vector_search import VectorSearchTool
from atlas_mcp.tools.composed.hybrid_search import HybridSearchTool
from atlas_mcp.tools.composed.semantic_search import SemanticSearchTool


class _StubEmbedder:
    async def embed(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


class _FakeVectorBackend:
    def __init__(self, matches):
        self.matches = matches

    async def search(self, collection, vector, top_k, query_filter):
        return self.matches[:top_k]


class _FakePostgresBackend:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, tenant, sql, params, max_rows):
        return self.rows[:max_rows]


class _FakeESBackend:
    def __init__(self, response):
        self.response = response

    async def search(self, tenant, index, body):
        return self.response


@pytest.mark.asyncio
async def test_semantic_search_hydrates_from_postgres() -> None:
    vector_tool = VectorSearchTool(
        backend=_FakeVectorBackend(
            [{"id": "d1", "score": 0.9, "payload": {"text": "chunk"}}]
        )
    )
    postgres_tool = PostgresQueryTool(
        backend=_FakePostgresBackend(
            [{"id": "d1", "title": "Doc One", "body": "Full body", "url": "http://x"}]
        )
    )
    tool = SemanticSearchTool(
        embedder=_StubEmbedder(), vector_tool=vector_tool, postgres_tool=postgres_tool
    )
    args = tool.validate({"query": "how to reset", "collection": "docs", "top_k": 3})
    result = await tool.run("acme", args)
    assert result["results"][0]["title"] == "Doc One"
    assert result["results"][0]["body"] == "Full body"


@pytest.mark.asyncio
async def test_semantic_search_without_hydration_returns_previews() -> None:
    vector_tool = VectorSearchTool(
        backend=_FakeVectorBackend(
            [{"id": "d1", "score": 0.8, "payload": {"text": "a preview", "lang": "en"}}]
        )
    )
    tool = SemanticSearchTool(embedder=_StubEmbedder(), vector_tool=vector_tool)
    args = tool.validate(
        {"query": "q", "collection": "docs", "hydrate_from_postgres": False}
    )
    result = await tool.run("acme", args)
    assert result["results"][0]["preview"] == "a preview"
    assert result["results"][0]["metadata"] == {"lang": "en"}


@pytest.mark.asyncio
async def test_semantic_search_empty_matches() -> None:
    tool = SemanticSearchTool(
        embedder=_StubEmbedder(), vector_tool=VectorSearchTool(backend=_FakeVectorBackend([]))
    )
    args = tool.validate({"query": "q", "collection": "docs"})
    result = await tool.run("acme", args)
    assert result["results"] == []


@pytest.mark.asyncio
async def test_hybrid_search_fuses_with_rrf() -> None:
    # d2 appears in both result sets and should rank first after fusion.
    es_tool = ElasticsearchSearchTool(
        backend=_FakeESBackend(
            {
                "hits": {
                    "total": {"value": 2},
                    "hits": [
                        {"_id": "d1", "_score": 5.0, "_source": {"title": "lexical one"}},
                        {"_id": "d2", "_score": 4.0, "_source": {"title": "shared"}},
                    ],
                },
                "took": 2,
            }
        )
    )
    vector_tool = VectorSearchTool(
        backend=_FakeVectorBackend(
            [
                {"id": "d2", "score": 0.95, "payload": {"text": "shared"}},
                {"id": "d3", "score": 0.80, "payload": {"text": "dense only"}},
            ]
        )
    )
    tool = HybridSearchTool(
        embedder=_StubEmbedder(), es_tool=es_tool, vector_tool=vector_tool
    )
    args = tool.validate(
        {"query": "shared topic", "es_index": "docs", "vector_collection": "docs", "top_k": 3}
    )
    result = await tool.run("acme", args)
    assert result["results"][0]["id"] == "d2"
    assert result["results"][0]["lexical_rank"] is not None
    assert result["results"][0]["dense_rank"] is not None
    assert result["fused_from"] == 3
