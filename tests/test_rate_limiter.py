"""Token-bucket rate limiter — burst, throttle, refill, per-key isolation."""

from __future__ import annotations

import pytest

from atlas_mcp.config import ServerSettings
from atlas_mcp.errors.framework import RateLimitError
from atlas_mcp.ratelimit.limiter import InMemoryTokenBucket, Quota, RateLimiter


def _limiter(**settings_kwargs) -> RateLimiter:
    settings = ServerSettings(**settings_kwargs)
    return RateLimiter(settings, backend=InMemoryTokenBucket())


@pytest.mark.asyncio
async def test_allows_within_burst() -> None:
    limiter = _limiter(rate_limit_burst=5, rate_limit_default_rpm=60)
    for _ in range(5):
        await limiter.acquire("acme", "postgres.query")


@pytest.mark.asyncio
async def test_throttles_after_burst_exhausted() -> None:
    limiter = _limiter(rate_limit_burst=3, rate_limit_default_rpm=60)
    for _ in range(3):
        await limiter.acquire("acme", "postgres.query")
    with pytest.raises(RateLimitError) as exc_info:
        await limiter.acquire("acme", "postgres.query")
    assert exc_info.value.code == "rate_limited"
    assert exc_info.value.retryable is True
    assert exc_info.value.context["retry_after_seconds"] > 0


@pytest.mark.asyncio
async def test_tenants_are_isolated() -> None:
    limiter = _limiter(rate_limit_burst=2, rate_limit_default_rpm=60)
    await limiter.acquire("acme", "postgres.query")
    await limiter.acquire("acme", "postgres.query")
    # A different tenant has its own bucket and is unaffected.
    await limiter.acquire("globex", "postgres.query")


@pytest.mark.asyncio
async def test_tools_are_isolated() -> None:
    limiter = _limiter(rate_limit_burst=1, rate_limit_default_rpm=60)
    await limiter.acquire("acme", "postgres.query")
    with pytest.raises(RateLimitError):
        await limiter.acquire("acme", "postgres.query")
    # A different tool has its own bucket.
    await limiter.acquire("acme", "vector.search")


@pytest.mark.asyncio
async def test_refill_restores_tokens_over_time() -> None:
    backend = InMemoryTokenBucket()
    # Manually seed a bucket, then consume with a later timestamp to force refill.
    # capacity 2, refill 60/min = 1/s.
    allowed, _ = await backend.consume("k", capacity=2, refill_per_s=1.0, cost=2, now_ms=0)
    assert allowed == 1
    denied, retry = await backend.consume("k", capacity=2, refill_per_s=1.0, cost=1, now_ms=0)
    assert denied == 0 and retry > 0
    # 1.5s later ~1.5 tokens have refilled → one more consume allowed.
    allowed_again, _ = await backend.consume(
        "k", capacity=2, refill_per_s=1.0, cost=1, now_ms=1500
    )
    assert allowed_again == 1


def test_expensive_tools_get_tighter_quota() -> None:
    limiter = _limiter(rate_limit_burst=20, rate_limit_default_rpm=60)
    assert limiter.quota_for("semantic_search") == Quota(capacity=30, refill_per_minute=120)
    assert limiter.quota_for("customer.build_context").capacity == 3
    assert limiter.quota_for("postgres.query").capacity == 20  # default burst
