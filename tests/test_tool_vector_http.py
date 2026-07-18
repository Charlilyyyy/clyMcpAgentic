"""Atomic vector search, embeddings, and HTTP fetch tools."""

from __future__ import annotations

import httpx
import pytest

from atlas_mcp.errors.framework import PolicyError, UpstreamError, ValidationError
from atlas_mcp.governance.http_allowlist import HttpAllowlist
from atlas_mcp.tools.atomic.embeddings import EmbeddingClient
from atlas_mcp.tools.atomic.http_client import HTTPFetchTool
from atlas_mcp.tools.atomic.vector_search import VectorSearchTool


class _FakeVector:
    def __init__(self, matches=None, error=None):
        self.matches = matches or []
        self.error = error
        self.last_filter = None

    async def search(self, collection, vector, top_k, query_filter):
        self.last_filter = query_filter
        if self.error is not None:
            raise self.error
        return self.matches


@pytest.mark.asyncio
async def test_vector_search_injects_tenant_filter() -> None:
    backend = _FakeVector(matches=[{"id": "d1", "score": 0.9, "payload": {"text": "hi"}}])
    tool = VectorSearchTool(backend=backend)
    args = tool.validate({"collection": "docs", "vector": [0.1, 0.2], "top_k": 3})
    result = await tool.run("acme", args)
    assert {"key": "tenant", "match": {"value": "acme"}} in backend.last_filter["must"]
    assert result["matches"][0]["id"] == "d1"


@pytest.mark.asyncio
async def test_vector_search_merges_user_filter() -> None:
    backend = _FakeVector(matches=[])
    tool = VectorSearchTool(backend=backend)
    args = tool.validate(
        {
            "collection": "docs",
            "vector": [0.1],
            "filter": {"must": [{"key": "lang", "match": {"value": "en"}}]},
        }
    )
    await tool.run("acme", args)
    keys = [clause.get("key") for clause in backend.last_filter["must"]]
    assert "tenant" in keys and "lang" in keys


@pytest.mark.asyncio
async def test_embeddings_client_parses_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2, 0.3]}]})

    client = EmbeddingClient(base_url="https://embed.local/v1", transport=httpx.MockTransport(handler))
    vectors = await client.embed(["hello"])
    assert vectors == [[0.1, 0.2, 0.3]]


@pytest.mark.asyncio
async def test_embeddings_client_maps_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    client = EmbeddingClient(base_url="https://embed.local/v1", transport=httpx.MockTransport(handler))
    with pytest.raises(UpstreamError) as exc_info:
        await client.embed(["hello"])
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_http_fetch_blocks_non_allowlisted_host() -> None:
    tool = HTTPFetchTool(allowlist=HttpAllowlist({"acme": ["api.stripe.com"]}))
    args = tool.validate({"url": "https://evil.example.com/steal"})
    with pytest.raises(PolicyError) as exc_info:
        await tool.run("acme", args)
    assert exc_info.value.code == "host_not_allowlisted"


@pytest.mark.asyncio
async def test_http_fetch_allows_allowlisted_host() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    tool = HTTPFetchTool(
        allowlist=HttpAllowlist({"acme": ["api.stripe.com"]}),
        transport=httpx.MockTransport(handler),
    )
    args = tool.validate({"url": "https://api.stripe.com/v1/charges"})
    result = await tool.run("acme", args)
    assert result["status"] == 200
    assert result["truncated"] is False


def test_http_fetch_rejects_non_https() -> None:
    tool = HTTPFetchTool(allowlist=HttpAllowlist({}))
    with pytest.raises(ValidationError):
        tool.validate({"url": "http://api.stripe.com"})
