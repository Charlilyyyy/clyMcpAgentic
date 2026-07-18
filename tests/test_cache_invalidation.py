"""Cache invalidation strategy — key-, tool-, and tenant-scoped drops."""

from __future__ import annotations

import pytest

from atlas_mcp.cache.manager import CacheManager, InMemoryL2
from atlas_mcp.config import ServerSettings


def _manager() -> CacheManager:
    return CacheManager(ServerSettings(), l2=InMemoryL2())


@pytest.mark.asyncio
async def test_invalidate_key_drops_single_entry() -> None:
    cache = _manager()
    await cache.set("atlas:acme:s3.get_object:abc", {"v": 1})
    await cache.invalidate_key("atlas:acme:s3.get_object:abc")
    assert await cache.get("atlas:acme:s3.get_object:abc") is None


@pytest.mark.asyncio
async def test_invalidate_tool_drops_matching_tool_only() -> None:
    cache = _manager()
    await cache.set("atlas:acme:s3.get_object:a", 1)
    await cache.set("atlas:acme:s3.get_object:b", 2)
    await cache.set("atlas:acme:postgres.query:c", 3)

    dropped = await cache.invalidate_tool("acme", "s3.get_object")
    assert dropped == 2
    assert await cache.get("atlas:acme:s3.get_object:a") is None
    assert await cache.get("atlas:acme:postgres.query:c") == 3


@pytest.mark.asyncio
async def test_invalidate_tenant_drops_all_tenant_entries() -> None:
    cache = _manager()
    await cache.set("atlas:acme:s3.get_object:a", 1)
    await cache.set("atlas:acme:postgres.query:b", 2)
    await cache.set("atlas:globex:s3.get_object:c", 3)

    dropped = await cache.invalidate_tenant("acme")
    assert dropped == 2
    assert await cache.get("atlas:globex:s3.get_object:c") == 3
