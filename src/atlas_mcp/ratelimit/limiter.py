"""Component 8 — Rate Limiting & Quotas.

A token bucket keyed on ``(tenant, tool)`` so noisy agents cannot starve other
tenants and runaway loops on one tool cannot starve another. Token bucket (not
fixed window) because it permits natural bursts — a burst of tool calls while
planning, then quiet while reasoning.

The refill/consume step is delegated to a :class:`TokenBucketBackend`:

* :class:`RedisTokenBucket` — the production backend. An atomic Lua script
  makes check-and-consume race-free across horizontally scaled replicas
  (an in-process counter would let an agent N-x its quota across N replicas).
* :class:`InMemoryTokenBucket` — a single-process backend for local dev/tests.

Both return ``(allowed, retry_after_ms)`` so the limiter stays backend-agnostic.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Protocol

from atlas_mcp.config import ServerSettings
from atlas_mcp.errors.framework import RateLimitError


@dataclass(frozen=True, slots=True)
class Quota:
    capacity: int          # Maximum tokens in the bucket (burst size).
    refill_per_minute: int  # Tokens replenished per minute (sustained rate).


class TokenBucketBackend(Protocol):
    async def consume(
        self, key: str, capacity: int, refill_per_s: float, cost: int, now_ms: int
    ) -> tuple[int, int]:
        """Return ``(allowed, retry_after_ms)``."""
        ...


class InMemoryTokenBucket:
    """Single-process token bucket — deterministic, dependency-free."""

    def __init__(self) -> None:
        self._buckets: dict[str, tuple[float, int]] = {}  # key -> (tokens, last_ms)

    async def consume(
        self, key: str, capacity: int, refill_per_s: float, cost: int, now_ms: int
    ) -> tuple[int, int]:
        tokens, last_ms = self._buckets.get(key, (float(capacity), now_ms))
        elapsed_s = max(0.0, (now_ms - last_ms) / 1000.0)
        tokens = min(capacity, tokens + elapsed_s * refill_per_s)

        if tokens >= cost:
            tokens -= cost
            self._buckets[key] = (tokens, now_ms)
            return 1, 0
        deficit = cost - tokens
        retry_after_ms = math.ceil((deficit / refill_per_s) * 1000) if refill_per_s else 3_600_000
        self._buckets[key] = (tokens, now_ms)
        return 0, retry_after_ms


_LUA_SCRIPT = """
local key         = KEYS[1]
local capacity    = tonumber(ARGV[1])
local refill_per_s= tonumber(ARGV[2])
local now_ms      = tonumber(ARGV[3])
local cost        = tonumber(ARGV[4])

local bucket = redis.call('HMGET', key, 'tokens', 'last_ms')
local tokens  = tonumber(bucket[1]) or capacity
local last_ms = tonumber(bucket[2]) or now_ms

local elapsed_s = math.max(0, (now_ms - last_ms) / 1000.0)
tokens = math.min(capacity, tokens + elapsed_s * refill_per_s)

local allowed = 0
local retry_after_ms = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  local deficit = cost - tokens
  retry_after_ms = math.ceil((deficit / refill_per_s) * 1000)
end

redis.call('HMSET', key, 'tokens', tokens, 'last_ms', now_ms)
redis.call('EXPIRE', key, 3600)

return {allowed, retry_after_ms}
"""


class RedisTokenBucket:
    """Redis-backed token bucket using an atomic Lua script."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis = None
        self._sha: str | None = None

    async def _ensure(self):
        if self._redis is None:
            from redis.asyncio import Redis

            self._redis = Redis.from_url(self._redis_url, decode_responses=True)
            self._sha = await self._redis.script_load(_LUA_SCRIPT)
        return self._redis

    async def consume(
        self, key: str, capacity: int, refill_per_s: float, cost: int, now_ms: int
    ) -> tuple[int, int]:
        redis = await self._ensure()
        allowed, retry_after_ms = await redis.evalsha(
            self._sha, 1, key, capacity, refill_per_s, now_ms, cost
        )
        return int(allowed), int(retry_after_ms)

    async def disconnect(self) -> None:
        if self._redis is not None:
            await self._redis.close()
            self._redis = None


class RateLimiter:
    """Per-(tenant, tool) token bucket over a pluggable backend."""

    def __init__(self, settings: ServerSettings, backend: TokenBucketBackend | None = None) -> None:
        self.settings = settings
        self._backend = backend
        self._default = Quota(
            capacity=settings.rate_limit_burst,
            refill_per_minute=settings.rate_limit_default_rpm,
        )
        self._overrides: dict[str, Quota] = {
            "customer.build_context": Quota(capacity=3, refill_per_minute=10),
            "semantic_search": Quota(capacity=30, refill_per_minute=120),
            "hybrid_search": Quota(capacity=30, refill_per_minute=120),
        }

    def _get_backend(self) -> TokenBucketBackend:
        if self._backend is None:
            self._backend = RedisTokenBucket(self.settings.redis_url)
        return self._backend

    def quota_for(self, tool: str) -> Quota:
        return self._overrides.get(tool, self._default)

    async def acquire(self, tenant: str, tool: str, cost: int = 1) -> None:
        """Consume ``cost`` tokens; raise :class:`RateLimitError` on exhaustion."""
        quota = self.quota_for(tool)
        refill_per_s = quota.refill_per_minute / 60.0
        key = f"atlas:rl:{tenant}:{tool}"
        now_ms = int(time.time() * 1000)

        allowed, retry_after_ms = await self._get_backend().consume(
            key, quota.capacity, refill_per_s, cost, now_ms
        )
        if allowed == 0:
            raise RateLimitError(retry_after_seconds=retry_after_ms / 1000.0)
