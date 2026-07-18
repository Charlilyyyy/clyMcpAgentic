"""Workflow tool integration — merged customer context, partial failures."""

from __future__ import annotations

import pytest

from atlas_mcp.tools.atomic.elasticsearch import ElasticsearchSearchTool
from atlas_mcp.tools.atomic.postgres import PostgresQueryTool
from atlas_mcp.tools.atomic.vector_search import VectorSearchTool
from atlas_mcp.tools.composed.semantic_search import SemanticSearchTool
from atlas_mcp.tools.workflow.customer_context import CustomerContextTool


class _StubEmbedder:
    async def embed(self, texts):
        return [[0.1, 0.2] for _ in texts]


class _RoutedPostgres:
    """Returns different rows depending on the query shape."""

    def __init__(self, *, fail: bool = False):
        self.fail = fail

    async def fetch(self, tenant, sql, params, max_rows):
        if self.fail:
            raise RuntimeError("postgres down")
        if "FROM customers" in sql:
            return [{"id": params[0], "name": "Ada", "email": "ada@acme.io", "tier": "gold"}]
        if "FROM orders" in sql:
            return [{"id": "o1", "status": "shipped", "total_cents": 1999, "currency": "USD"}]
        return []


class _FakeES:
    def __init__(self, *, fail: bool = False):
        self.fail = fail

    async def search(self, tenant, index, body):
        if self.fail:
            raise RuntimeError("es down")
        return {
            "hits": {
                "total": {"value": 1},
                "hits": [
                    {"_id": "t1", "_score": 1.0, "_source": {"subject": "help", "status": "open"}}
                ],
            },
            "took": 1,
        }


class _FakeVector:
    async def search(self, collection, vector, top_k, query_filter):
        return [{"id": "doc1", "score": 0.9, "payload": {"text": "reset instructions"}}]


def _build_tool(*, pg_fail=False, es_fail=False) -> CustomerContextTool:
    semantic = SemanticSearchTool(
        embedder=_StubEmbedder(), vector_tool=VectorSearchTool(backend=_FakeVector())
    )
    return CustomerContextTool(
        postgres_tool=PostgresQueryTool(backend=_RoutedPostgres(fail=pg_fail)),
        es_tool=ElasticsearchSearchTool(backend=_FakeES(fail=es_fail)),
        semantic_tool=semantic,
    )


@pytest.mark.asyncio
async def test_workflow_merges_context_in_one_call() -> None:
    tool = _build_tool()
    args = tool.validate({"customer_id": "cust-1", "question": "how do I reset?"})
    result = await tool.run("acme", args)

    assert result["customer_id"] == "cust-1"
    assert result["profile"]["name"] == "Ada"
    assert result["orders"][0]["status"] == "shipped"
    assert result["tickets"][0]["id"] == "t1"
    assert result["docs"][0]["id"] == "doc1"
    assert "partial_errors" not in result


@pytest.mark.asyncio
async def test_workflow_returns_partial_context_on_backend_failure() -> None:
    tool = _build_tool(es_fail=True)
    args = tool.validate({"customer_id": "cust-1", "question": "status?"})
    result = await tool.run("acme", args)

    # Postgres-backed pieces still present; the ES-backed piece is flagged.
    assert result["profile"]["name"] == "Ada"
    assert result["orders"]
    assert "tickets" not in result
    assert "tickets" in result["partial_errors"]


@pytest.mark.asyncio
async def test_workflow_respects_include_flags() -> None:
    tool = _build_tool()
    args = tool.validate(
        {
            "customer_id": "cust-1",
            "question": "q",
            "include_orders": False,
            "include_tickets": False,
            "include_docs": False,
        }
    )
    result = await tool.run("acme", args)
    assert "orders" not in result
    assert "tickets" not in result
    assert "docs" not in result
    assert result["profile"]["name"] == "Ada"
