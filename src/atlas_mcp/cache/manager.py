"""Component 9 — Two-tier cache manager (L1 in-process + L2 shared).

Reads check L1 → L2 → miss. Writes populate both tiers (write-through). The L2
tier is a pluggable :class:`L2Backend`:

* :class:`RedisL2` — shared across replicas, survives deploys (~1 ms hits).
* :class:`InMemoryL2` — single-process backend for local dev/tests.

Cache-stampede protection: when many agents miss the same key at once, only
one should recompute. :meth:`get_or_compute` takes a short L2 lock keyed on the
cache key; losers wait briefly and re-read (single-flight).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Awaitable, Callable, Protocol

from atlas_mcp.cache.l1 import L1Cache
from atlas_mcp.config import ServerSettings


class L2Backend(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl_seconds: int) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def delete_prefix(self, prefix: str) -> int: ...
    async def acquire_lock(self, lock_key: str, ttl_ms: int) -> bool: ...
    async def release_lock(self, lock_key: str) -> None: ...


class InMemoryL2:
    """Single-process L2 for tests — mimics the Redis surface we use."""

    def __init__(self, *, clock=time.monotonic) -> None:
        self._store: dict[str, tuple[str, float]] = {}
        self._locks: dict[str, float] = {}
        self._clock = clock

    async def get(self, key: str) -> str | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at < self._clock():
            self._store.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._store[key] = (value, self._clock() + ttl_seconds)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def delete_prefix(self, prefix: str) -> int:
        matched = [k for k in self._store if k.startswith(prefix)]
        for k in matched:
            self._store.pop(k, None)
        return len(matched)

    async def acquire_lock(self, lock_key: str, ttl_ms: int) -> bool:
        expires = self._locks.get(lock_key)
        now = self._clock()
        if expires is not None and expires > now:
            return False
        self._locks[lock_key] = now + ttl_ms / 1000.0
        return True

    async def release_lock(self, lock_key: str) -> None:
        self._locks.pop(lock_key, None)


class RedisL2:
    """Redis-backed L2 tier."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis = None

    async def _ensure(self):
        if self._redis is None:
            from redis.asyncio import Redis

            self._redis = Redis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    async def get(self, key: str) -> str | None:
        return await (await self._ensure()).get(key)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        await (await self._ensure()).set(key, value, ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        await (await self._ensure()).delete(key)

    async def delete_prefix(self, prefix: str) -> int:
        redis = await self._ensure()
        deleted = 0
        async for key in redis.scan_iter(match=f"{prefix}*"):
            await redis.delete(key)
            deleted += 1
        return deleted

    async def acquire_lock(self, lock_key: str, ttl_ms: int) -> bool:
        return bool(await (await self._ensure()).set(lock_key, "1", nx=True, px=ttl_ms))

    async def release_lock(self, lock_key: str) -> None:
        await (await self._ensure()).delete(lock_key)

    async def disconnect(self) -> None:
        if self._redis is not None:
            await self._redis.close()
            self._redis = None


class CacheManager:
    """Coordinates L1 + L2 with write-through and stampede locks."""

    STAMPEDE_LOCK_TTL_MS = 5_000
    STAMPEDE_WAIT_MS = 25
    STAMPEDE_MAX_WAITS = 40

    def __init__(self, settings: ServerSettings, l2: L2Backend | None = None) -> None:
        self.settings = settings
        self.l1 = L1Cache(max_items=settings.cache_l1_max_items)
        self._l2 = l2
        self.hits = 0
        self.misses = 0

    def _get_l2(self) -> L2Backend:
        if self._l2 is None:
            self._l2 = RedisL2(self.settings.redis_url)
        return self._l2

    async def get(self, key: str) -> Any | None:
        if (hit := await self.l1.get(key)) is not None:
            self.hits += 1
            return hit
        raw = await self._get_l2().get(key)
        if raw is not None:
            value = json.loads(raw)
            await self.l1.set(key, value, ttl_seconds=self.settings.cache_l1_ttl_seconds)
            self.hits += 1
            return value
        self.misses += 1
        return None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        ttl_l2 = ttl or self.settings.cache_l2_ttl_seconds
        ttl_l1 = min(ttl_l2, self.settings.cache_l1_ttl_seconds)
        await self.l1.set(key, value, ttl_seconds=ttl_l1)
        await self._get_l2().set(key, json.dumps(value, default=str), ttl_seconds=ttl_l2)

    async def delete(self, key: str) -> None:
        await self.l1.delete(key)
        await self._get_l2().delete(key)

    async def get_or_compute(
        self, key: str, compute: Callable[[], Awaitable[Any]], ttl: int | None = None
    ) -> Any:
        """Read-through with single-flight semantics on a miss."""
        if (hit := await self.get(key)) is not None:
            return hit

        l2 = self._get_l2()
        lock_key = f"{key}:lock"
        if await l2.acquire_lock(lock_key, self.STAMPEDE_LOCK_TTL_MS):
            try:
                value = await compute()
                await self.set(key, value, ttl=ttl)
                return value
            finally:
                await l2.release_lock(lock_key)

        # Someone else is computing — wait briefly, then re-read.
        for _ in range(self.STAMPEDE_MAX_WAITS):
            await asyncio.sleep(self.STAMPEDE_WAIT_MS / 1000)
            if (hit := await self.get(key)) is not None:
                return hit
        # Lock holder died or is slow — compute ourselves rather than hang.
        value = await compute()
        await self.set(key, value, ttl=ttl)
        return value

    # ── Invalidation strategy ─────────────────────────────────────────────
    # Cache keys are ``atlas:{tenant}:{tool}:{args_hash}``. That prefix layout
    # supports three levels of invalidation, cheapest to most surgical:
    #
    #   1. TTL expiry (default): every entry self-expires; correctness bounded
    #      by ``cache_l2_ttl_seconds``. This is the baseline — no code needed.
    #   2. Write-triggered, tool-scoped: after a destructive tool mutates data
    #      for a tenant, call ``invalidate_tool(tenant, read_tool)`` to drop the
    #      matching read cache (e.g. ``s3.put_object`` → drop ``s3.get_object``).
    #   3. Tenant-wide: ``invalidate_tenant(tenant)`` on a tenant-level event
    #      (data reset, GDPR delete) drops every entry for that tenant.
    async def invalidate_key(self, key: str) -> None:
        await self.delete(key)

    async def invalidate_tool(self, tenant: str, tool: str) -> int:
        prefix = f"atlas:{tenant}:{tool}:"
        return await self._invalidate_prefix(prefix)

    async def invalidate_tenant(self, tenant: str) -> int:
        prefix = f"atlas:{tenant}:"
        return await self._invalidate_prefix(prefix)

    async def _invalidate_prefix(self, prefix: str) -> int:
        await self.l1.delete_prefix(prefix)
        return await self._get_l2().delete_prefix(prefix)

    def stats(self) -> dict[str, Any]:
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_ratio": round(self.hits / total, 4) if total else 0.0,
            "l1_size": len(self.l1),
        }
