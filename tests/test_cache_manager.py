"""Two-tier cache manager — L1/L2 promotion, write-through, single-flight."""

from __future__ import annotations

import asyncio

import pytest

from atlas_mcp.cache.manager import CacheManager, InMemoryL2
from atlas_mcp.config import ServerSettings


def _manager(l2: InMemoryL2 | None = None) -> CacheManager:
    return CacheManager(ServerSettings(), l2=l2 or InMemoryL2())


@pytest.mark.asyncio
async def test_write_through_populates_both_tiers() -> None:
    l2 = InMemoryL2()
    cache = _manager(l2)
    await cache.set("k", {"v": 1})
    assert await cache.l1.get("k") == {"v": 1}
    assert await l2.get("k") is not None


@pytest.mark.asyncio
async def test_l2_hit_promotes_to_l1() -> None:
    l2 = InMemoryL2()
    cache = _manager(l2)
    await l2.set("k", '{"v": 2}', ttl_seconds=60)
    assert await cache.l1.get("k") is None  # not in L1 yet

    value = await cache.get("k")
    assert value == {"v": 2}
    assert await cache.l1.get("k") == {"v": 2}  # promoted


@pytest.mark.asyncio
async def test_miss_returns_none_and_counts() -> None:
    cache = _manager()
    assert await cache.get("absent") is None
    assert cache.stats()["misses"] == 1
    assert cache.stats()["hits"] == 0


@pytest.mark.asyncio
async def test_hit_ratio_tracking() -> None:
    cache = _manager()
    await cache.set("k", 1)
    await cache.get("k")
    await cache.get("k")
    await cache.get("absent")
    stats = cache.stats()
    assert stats["hits"] == 2
    assert stats["misses"] == 1
    assert stats["hit_ratio"] == pytest.approx(2 / 3, abs=0.01)


@pytest.mark.asyncio
async def test_delete_clears_both_tiers() -> None:
    l2 = InMemoryL2()
    cache = _manager(l2)
    await cache.set("k", "v")
    await cache.delete("k")
    assert await cache.l1.get("k") is None
    assert await l2.get("k") is None


@pytest.mark.asyncio
async def test_get_or_compute_single_flight() -> None:
    cache = _manager()
    compute_count = 0

    async def compute():
        nonlocal compute_count
        compute_count += 1
        await asyncio.sleep(0.02)
        return {"computed": True}

    # Fire many concurrent requests for the same missing key.
    results = await asyncio.gather(*[cache.get_or_compute("hot", compute) for _ in range(10)])
    assert all(r == {"computed": True} for r in results)
    assert compute_count == 1  # only one caller computed; the rest waited and re-read
