"""Backend adapter protocols for atomic tools.

Atomic tools own *what* to fetch and *how to shape* the result; the backend
adapters own *how to talk to* Postgres, Elasticsearch, S3, and the vector DB.

Injecting a backend lets tests drive tools with in-memory fakes while
production wires the real async clients. The real adapters import their heavy
drivers lazily, so importing this module never pulls in asyncpg/boto3 unless
a real connection is actually opened.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PostgresBackend(Protocol):
    async def fetch(
        self, tenant: str, sql: str, params: list[Any], max_rows: int
    ) -> list[dict[str, Any]]:
        """Run a read-only query scoped to ``tenant`` and return row dicts."""
        ...


@runtime_checkable
class ElasticsearchBackend(Protocol):
    async def search(self, tenant: str, index: str, body: dict[str, Any]) -> dict[str, Any]:
        """Run a search and return the raw Elasticsearch response."""
        ...


@runtime_checkable
class ObjectStoreBackend(Protocol):
    async def get_object(self, bucket: str, key: str, max_bytes: int) -> bytes:
        ...

    async def put_object(
        self, bucket: str, key: str, body: bytes, content_type: str, metadata: dict[str, str]
    ) -> None:
        ...


@runtime_checkable
class VectorBackend(Protocol):
    async def search(
        self, collection: str, vector: list[float], top_k: int, query_filter: dict[str, Any]
    ) -> list[dict[str, Any]]:
        ...


@runtime_checkable
class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...
