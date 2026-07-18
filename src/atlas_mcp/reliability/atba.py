"""Component 7 — Adaptive Timeout Budget Allocation (ATBA).

An agent that chains five tools with a 10-second timeout each can block for 50
seconds before giving up — fine in isolation, a disaster inside a user-facing
chat. ATBA allocates a *total* budget for the whole agent turn and spends it
across tool calls proportional to each tool's observed latency.

The server tracks p95 latency per tool; each call's timeout is
``max(MIN, min(p95 * safety, remaining_budget / calls_left))``. Fast tools do
not over-reserve; when the budget runs tight, every call shrinks.
"""

from __future__ import annotations

import asyncio
import contextvars
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from atlas_mcp.errors.framework import TimeoutError_


@dataclass
class BudgetContext:
    """Lifespan of a single agent request's time budget."""

    total_budget_s: float
    started_at: float
    calls_made: int = 0
    spent_s: float = 0.0

    @property
    def remaining_s(self) -> float:
        return max(0.0, self.total_budget_s - (time.monotonic() - self.started_at))


_current_budget: contextvars.ContextVar[BudgetContext | None] = contextvars.ContextVar(
    "atlas_budget", default=None
)


class LatencyTracker:
    """Rolling window of per-tool durations for p95 estimation."""

    def __init__(self, window: int = 500) -> None:
        self.window = window
        self._samples: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=window))

    def record(self, tool: str, duration_s: float) -> None:
        self._samples[tool].append(duration_s)

    def p95(self, tool: str, default_s: float = 5.0) -> float:
        samples = self._samples.get(tool)
        if not samples or len(samples) < 20:
            return default_s
        ordered = sorted(samples)
        idx = int(len(ordered) * 0.95)
        return ordered[min(idx, len(ordered) - 1)]


class ATBA:
    """Allocates tool-call timeouts from a shared per-turn budget."""

    SAFETY_FACTOR = 1.5
    MIN_CALL_TIMEOUT_S = 0.5
    EXPECTED_CALLS_PER_TURN = 5

    def __init__(self, total_budget_ms: int) -> None:
        self.total_budget_s = total_budget_ms / 1000.0
        self.tracker = LatencyTracker()

    def begin(self) -> BudgetContext:
        ctx = BudgetContext(total_budget_s=self.total_budget_s, started_at=time.monotonic())
        _current_budget.set(ctx)
        return ctx

    def timeout_for(self, tool: str) -> float:
        ctx = _current_budget.get()
        target = self.tracker.p95(tool) * self.SAFETY_FACTOR
        if ctx is None:
            return max(self.MIN_CALL_TIMEOUT_S, target)
        calls_left = max(1, self.EXPECTED_CALLS_PER_TURN - ctx.calls_made)
        fair_share = ctx.remaining_s / calls_left
        return max(self.MIN_CALL_TIMEOUT_S, min(target, fair_share))

    async def call_with_budget(self, tool: str, coro) -> object:
        """Run ``coro`` under the per-call timeout derived from the budget."""
        timeout = self.timeout_for(tool)
        ctx = _current_budget.get()
        if ctx is not None:
            ctx.calls_made += 1

        started = time.monotonic()
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise TimeoutError_(tool=tool, budget_ms=int(timeout * 1000)) from exc
        finally:
            duration = time.monotonic() - started
            self.tracker.record(tool, duration)
            if ctx is not None:
                ctx.spent_s += duration
