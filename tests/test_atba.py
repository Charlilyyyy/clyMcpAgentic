"""ATBA — budget allocation, latency tracking, per-call timeout enforcement."""

from __future__ import annotations

import asyncio

import pytest

from atlas_mcp.errors.framework import TimeoutError_
from atlas_mcp.reliability.atba import ATBA, LatencyTracker


def test_latency_tracker_uses_default_until_enough_samples() -> None:
    tracker = LatencyTracker()
    assert tracker.p95("postgres.query", default_s=3.0) == 3.0
    for _ in range(19):
        tracker.record("postgres.query", 0.1)
    assert tracker.p95("postgres.query", default_s=3.0) == 3.0  # still < 20 samples


def test_latency_tracker_estimates_p95() -> None:
    tracker = LatencyTracker()
    for i in range(100):
        tracker.record("t", 0.01 * i)
    p95 = tracker.p95("t")
    assert 0.9 <= p95 <= 1.0


def test_timeout_without_budget_uses_p95_target() -> None:
    atba = ATBA(total_budget_ms=10_000)
    # No budget context started → falls back to p95 * safety, floored at MIN.
    assert atba.timeout_for("unknown") == pytest.approx(5.0 * 1.5)


def test_timeout_capped_by_fair_share_of_budget() -> None:
    # Fresh 10s budget, 5 expected calls → fair share 2s, below the 7.5s p95 cap.
    atba = ATBA(total_budget_ms=10_000)
    atba.begin()
    assert atba.timeout_for("t") == pytest.approx(2.0, abs=0.05)


def test_budget_shrinks_timeout_as_time_elapses() -> None:
    import time

    atba = ATBA(total_budget_ms=10_000)
    ctx = atba.begin()
    baseline = atba.timeout_for("t")
    # Simulate 9s already elapsed → only ~1s of budget left to share.
    ctx.started_at = time.monotonic() - 9.0
    later = atba.timeout_for("t")
    assert later < baseline
    assert later >= atba.MIN_CALL_TIMEOUT_S


@pytest.mark.asyncio
async def test_call_with_budget_times_out_slow_call() -> None:
    atba = ATBA(total_budget_ms=1_000)
    atba.begin()

    async def slow():
        await asyncio.sleep(5)

    # Force a tiny timeout by exhausting the notion of remaining budget.
    with pytest.raises(TimeoutError_) as exc_info:
        await asyncio.wait_for(atba.call_with_budget("t", slow()), timeout=2)
    assert exc_info.value.code == "timeout"


@pytest.mark.asyncio
async def test_call_with_budget_records_latency_and_returns() -> None:
    atba = ATBA(total_budget_ms=10_000)
    ctx = atba.begin()

    async def fast():
        return {"ok": True}

    result = await atba.call_with_budget("t", fast())
    assert result == {"ok": True}
    assert ctx.calls_made == 1
    assert ctx.spent_s >= 0.0
