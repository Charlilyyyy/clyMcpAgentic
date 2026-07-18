"""Real Qdrant HTTP vector backend (uses shared httpx)."""

from __future__ import annotations

from typing import Any

import httpx

from atlas_mcp.errors.framework import UpstreamError


class QdrantHttpBackend:
    def __init__(self, base_url: str, timeout_s: float = 2.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s

    async def search(
        self, collection: str, vector: list[float], top_k: int, query_filter: dict[str, Any]
    ) -> list[dict[str, Any]]:
        url = f"{self._base_url}/collections/{collection}/points/search"
        body = {"vector": vector, "limit": top_k, "filter": query_filter, "with_payload": True}
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                resp = await client.post(url, json=body)
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise UpstreamError(
                "vector_http_error",
                retryable=exc.response.status_code in (502, 503, 504),
                hint=f"vector db returned {exc.response.status_code}",
                context={"status": exc.response.status_code},
            ) from exc
        except httpx.RequestError as exc:
            raise UpstreamError("vector_network_error", retryable=True, hint=str(exc)) from exc
        return resp.json().get("result", [])
