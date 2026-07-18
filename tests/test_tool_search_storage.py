"""Atomic Elasticsearch and S3 tools — tenant isolation and error mapping."""

from __future__ import annotations

from typing import Any

import pytest

from atlas_mcp.errors.framework import UpstreamError
from atlas_mcp.tools.atomic.elasticsearch import ElasticsearchSearchTool
from atlas_mcp.tools.atomic.s3_storage import S3GetTool, S3PutTool


class _FakeES:
    def __init__(self, response: dict | None = None, error: Exception | None = None):
        self.response = response or {"hits": {"total": {"value": 0}, "hits": []}, "took": 1}
        self.error = error
        self.last_body: dict[str, Any] | None = None

    async def search(self, tenant, index, body):
        self.last_body = body
        if self.error is not None:
            raise self.error
        return self.response


class _FakeStore:
    def __init__(self, data: dict[str, bytes] | None = None, error: Exception | None = None):
        self.data = data or {}
        self.error = error
        self.puts: list[tuple[str, str, bytes]] = []

    async def get_object(self, bucket, key, max_bytes):
        if self.error is not None:
            raise self.error
        return self.data[key][:max_bytes]

    async def put_object(self, bucket, key, body, content_type, metadata):
        if self.error is not None:
            raise self.error
        self.puts.append((bucket, key, body))
        self.data[key] = body


@pytest.mark.asyncio
async def test_es_injects_mandatory_tenant_filter() -> None:
    backend = _FakeES(
        response={
            "hits": {
                "total": {"value": 1},
                "hits": [{"_id": "t1", "_score": 1.2, "_source": {"subject": "hi"}}],
            },
            "took": 3,
        }
    )
    tool = ElasticsearchSearchTool(backend=backend)
    args = tool.validate({"index": "tickets", "query": {"match_all": {}}, "size": 5})
    result = await tool.run("acme", args)

    filters = backend.last_body["query"]["bool"]["filter"]
    assert {"term": {"_tenant": "acme"}} in filters
    assert result["total"] == 1
    assert result["hits"][0]["id"] == "t1"


@pytest.mark.asyncio
async def test_es_error_is_mapped_with_retryable_status() -> None:
    err = Exception("boom")
    err.status_code = 503  # type: ignore[attr-defined]
    tool = ElasticsearchSearchTool(backend=_FakeES(error=err))
    args = tool.validate({"index": "tickets", "query": {"match_all": {}}})
    with pytest.raises(UpstreamError) as exc_info:
        await tool.run("acme", args)
    assert exc_info.value.code == "elasticsearch_error"
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_s3_get_prefixes_key_with_tenant() -> None:
    store = _FakeStore(data={"acme/docs/readme.txt": b"hello world"})
    tool = S3GetTool(backend=store)
    args = tool.validate({"key": "docs/readme.txt"})
    result = await tool.run("acme", args)
    assert result["content"] == "hello world"
    assert result["size_bytes"] == 11


@pytest.mark.asyncio
async def test_s3_get_missing_key_maps_to_not_found() -> None:
    err = Exception("missing")
    err.response = {"Error": {"Code": "NoSuchKey"}}  # type: ignore[attr-defined]
    tool = S3GetTool(backend=_FakeStore(error=err))
    args = tool.validate({"key": "docs/missing.txt"})
    with pytest.raises(UpstreamError) as exc_info:
        await tool.run("acme", args)
    assert exc_info.value.code == "not_found"
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_s3_put_is_tenant_prefixed_and_destructive() -> None:
    store = _FakeStore()
    tool = S3PutTool(backend=store)
    assert tool.meta.destructive is True
    args = tool.validate({"key": "attachments/a.txt", "content": "data"})
    result = await tool.run("globex", args)
    assert result["bytes_written"] == 4
    assert store.puts[0][1] == "globex/attachments/a.txt"
