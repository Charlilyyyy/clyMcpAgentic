"""Retry policy — retryable-only, backoff, deadline awareness."""

from __future__ import annotations

import pytest

from atlas_mcp.errors.framework import UpstreamError, ValidationError
from atlas_mcp.reliability.retry import with_retry


async def _noop_sleep(_delay: float) -> None:
    return None


@pytest.mark.asyncio
async def test_succeeds_without_retry() -> None:
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        return "ok"

    result = await with_retry(fn, sleep=_noop_sleep)
    assert result == "ok"
    assert calls == 1


@pytest.mark.asyncio
async def test_retries_transient_then_succeeds() -> None:
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise UpstreamError(code="transient", retryable=True)
        return "recovered"

    result = await with_retry(fn, max_attempts=5, sleep=_noop_sleep)
    assert result == "recovered"
    assert calls == 3


@pytest.mark.asyncio
async def test_does_not_retry_non_retryable() -> None:
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        raise ValidationError(code="bad_input", retryable=False)

    with pytest.raises(ValidationError):
        await with_retry(fn, max_attempts=5, sleep=_noop_sleep)
    assert calls == 1


@pytest.mark.asyncio
async def test_exhausts_attempts_and_raises_last() -> None:
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        raise UpstreamError(code="transient", retryable=True)

    with pytest.raises(UpstreamError):
        await with_retry(fn, max_attempts=3, sleep=_noop_sleep)
    assert calls == 3


@pytest.mark.asyncio
async def test_deadline_stops_retry_early() -> None:
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        raise UpstreamError(code="transient", retryable=True)

    # Deadline already in the past → no sleeping allowed → fail after first try.
    with pytest.raises(UpstreamError):
        await with_retry(fn, max_attempts=5, deadline_s=0.0, sleep=_noop_sleep)
    assert calls == 1
