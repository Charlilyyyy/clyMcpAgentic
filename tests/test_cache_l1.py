"""L1 in-process cache — TTL expiry and LRU eviction."""

from __future__ import annotations

import pytest

from atlas_mcp.cache.l1 import L1Cache


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.asyncio
async def test_set_and_get_roundtrip() -> None:
    cache = L1Cache(max_items=10)
    await cache.set("k", {"v": 1}, ttl_seconds=60)
    assert await cache.get("k") == {"v": 1}


@pytest.mark.asyncio
async def test_missing_key_returns_none() -> None:
    cache = L1Cache(max_items=10)
    assert await cache.get("nope") is None


@pytest.mark.asyncio
async def test_entry_expires_after_ttl() -> None:
    clock = _FakeClock()
    cache = L1Cache(max_items=10, clock=clock)
    await cache.set("k", "v", ttl_seconds=30)
    clock.advance(31)
    assert await cache.get("k") is None


@pytest.mark.asyncio
async def test_lru_evicts_oldest_when_full() -> None:
    cache = L1Cache(max_items=2)
    await cache.set("a", 1, ttl_seconds=60)
    await cache.set("b", 2, ttl_seconds=60)
    await cache.get("a")  # touch a → b becomes LRU
    await cache.set("c", 3, ttl_seconds=60)
    assert await cache.get("b") is None
    assert await cache.get("a") == 1
    assert await cache.get("c") == 3


@pytest.mark.asyncio
async def test_delete_removes_entry() -> None:
    cache = L1Cache(max_items=10)
    await cache.set("k", "v", ttl_seconds=60)
    await cache.delete("k")
    assert await cache.get("k") is None
