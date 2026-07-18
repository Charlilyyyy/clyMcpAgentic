"""Circuit breaker state machine — open, half-open, close transitions."""

from __future__ import annotations

import asyncio

import pytest

from atlas_mcp.config import ServerSettings
from atlas_mcp.errors.framework import CircuitOpenError, UpstreamError, ValidationError
from atlas_mcp.reliability.circuit_breaker import CircuitBreaker, CircuitBreakerRegistry, State


async def _transient_failure():
    raise UpstreamError(code="transient", retryable=True, hint="backend down")


async def _deterministic_failure():
    raise ValidationError(code="bad_input", retryable=False)


async def _success():
    return {"ok": True}


@pytest.mark.asyncio
async def test_opens_after_threshold_transient_failures() -> None:
    cb = CircuitBreaker("test", failure_threshold=3, recovery_seconds=1)
    for _ in range(3):
        with pytest.raises(UpstreamError):
            await cb.call(_transient_failure)
    assert cb.state is State.OPEN
    assert cb.trips == 1

    with pytest.raises(CircuitOpenError):
        await cb.call(_transient_failure)


@pytest.mark.asyncio
async def test_deterministic_errors_do_not_open_circuit() -> None:
    cb = CircuitBreaker("test", failure_threshold=3, recovery_seconds=1)
    for _ in range(10):
        with pytest.raises(ValidationError):
            await cb.call(_deterministic_failure)
    assert cb.state is State.CLOSED


@pytest.mark.asyncio
async def test_half_open_probe_closes_on_success() -> None:
    cb = CircuitBreaker("test", failure_threshold=2, recovery_seconds=1)
    for _ in range(2):
        with pytest.raises(UpstreamError):
            await cb.call(_transient_failure)
    assert cb.state is State.OPEN

    await asyncio.sleep(1.1)

    result = await cb.call(_success)
    assert result == {"ok": True}
    assert cb.state is State.CLOSED


@pytest.mark.asyncio
async def test_half_open_probe_reopens_on_failure() -> None:
    cb = CircuitBreaker("test", failure_threshold=2, recovery_seconds=1)
    for _ in range(2):
        with pytest.raises(UpstreamError):
            await cb.call(_transient_failure)

    await asyncio.sleep(1.1)

    with pytest.raises(UpstreamError):
        await cb.call(_transient_failure)
    assert cb.state is State.OPEN
    assert cb.trips == 2


@pytest.mark.asyncio
async def test_registry_returns_one_breaker_per_tool() -> None:
    registry = CircuitBreakerRegistry(ServerSettings())
    a = registry.for_tool("postgres.query")
    b = registry.for_tool("postgres.query")
    c = registry.for_tool("s3.get_object")
    assert a is b
    assert a is not c
    assert registry.snapshot()["postgres.query"] == "closed"
