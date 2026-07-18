"""Load / concurrency behaviour of the rate limiter and cache.

These are not micro-benchmarks — they assert *correctness under
concurrency*: that per-tenant isolation holds when many tenants hammer the
limiter at once, that the token bucket never over-admits beyond capacity,
and that the cache's single-flight guard collapses a concurrent stampede
into exactly one computation per key even across many hot keys.
"""

from __future__ import annotations

import asyncio

import pytest

from atlas_mcp.cache.manager import CacheManager, InMemoryL2
from atlas_mcp.config import ServerSettings
from atlas_mcp.errors.framework import RateLimitError
from atlas_mcp.ratelimit.limiter import InMemoryTokenBucket, RateLimiter


def _limiter(**kwargs) -> RateLimiter:
    return RateLimiter(ServerSettings(**kwargs), backend=InMemoryTokenBucket())


async def _try_acquire(limiter: RateLimiter, tenant: str, tool: str) -> bool:
    try:
        await limiter.acquire(tenant, tool)
        return True
    except RateLimitError:
        return False


@pytest.mark.asyncio
async def test_bucket_never_over_admits_under_concurrent_burst() -> None:
    """Fire 100 concurrent requests at a burst-10 bucket; exactly 10 pass."""
    limiter = _limiter(rate_limit_burst=10, rate_limit_default_rpm=0)
    outcomes = await asyncio.gather(
        *[_try_acquire(limiter, "acme", "postgres.query") for _ in range(100)]
    )
    assert sum(outcomes) == 10


@pytest.mark.asyncio
async def test_many_tenants_stay_isolated_under_load() -> None:
    """Each of 50 tenants gets its own burst; one tenant cannot starve another."""
    limiter = _limiter(rate_limit_burst=3, rate_limit_default_rpm=0)
    tenants = [f"tenant-{i}" for i in range(50)]

    async def drain(tenant: str) -> int:
        results = await asyncio.gather(
            *[_try_acquire(limiter, tenant, "postgres.query") for _ in range(10)]
        )
        return sum(results)

    admitted = await asyncio.gather(*[drain(t) for t in tenants])
    assert admitted == [3] * 50  # every tenant independently gets exactly its burst


@pytest.mark.asyncio
async def test_cache_single_flight_holds_across_many_hot_keys() -> None:
    """A stampede over 20 keys × 25 callers computes each key exactly once."""
    cache = CacheManager(ServerSettings(), l2=InMemoryL2())
    compute_counts: dict[str, int] = {}
    lock = asyncio.Lock()

    def make_compute(key: str):
        async def compute():
            async with lock:
                compute_counts[key] = compute_counts.get(key, 0) + 1
            await asyncio.sleep(0.01)
            return {"key": key}

        return compute

    keys = [f"hot-{i}" for i in range(20)]
    tasks = [cache.get_or_compute(k, make_compute(k)) for k in keys for _ in range(25)]
    results = await asyncio.gather(*tasks)

    assert len(results) == 20 * 25
    assert all(count == 1 for count in compute_counts.values())
    assert set(compute_counts) == set(keys)
