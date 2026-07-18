"""Real Elasticsearch adapter (lazily imported)."""

from __future__ import annotations

from typing import Any


class ElasticsearchClientBackend:
    def __init__(self, url: str) -> None:
        self._url = url
        self._client: Any = None

    def _es(self):
        if self._client is None:
            from elasticsearch import AsyncElasticsearch

            self._client = AsyncElasticsearch(self._url)
        return self._client

    async def search(self, tenant: str, index: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._es().search(index=index, body=body)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
