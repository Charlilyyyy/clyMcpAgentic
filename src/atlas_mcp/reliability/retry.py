"""Component 7 — Retry policy.

Only retryable errors get retried. A retryable error is one whose
:class:`ToolError.retryable` attribute is True — rate limits, 5xx, timeouts,
circuit probes. Everything else fails fast so the agent can adapt instead of
hammering a backend that will keep rejecting the same input.

Retries use capped full-jitter exponential backoff and respect an optional
per-call deadline (an ATBA budget); the loop will not sleep past it.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, Awaitable, Callable

from atlas_mcp.errors.framework import ToolError


async def with_retry(
    fn: Callable[..., Awaitable[Any]],
    *args,
    max_attempts: int = 3,
    base_delay_ms: int = 100,
    max_delay_ms: int = 2_000,
    deadline_s: float | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    **kwargs,
) -> Any:
    """Call ``fn`` with capped exponential backoff and full jitter.

    Parameters
    ----------
    deadline_s
        Absolute deadline in ``asyncio`` loop-time units. If set, the loop
        raises instead of sleeping past it.
    sleep
        Injectable sleep coroutine (tests pass a no-op to avoid real waits).
    """
    last_exc: ToolError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn(*args, **kwargs)
        except ToolError as exc:
            last_exc = exc
            if not exc.retryable or attempt == max_attempts:
                raise
            delay = _backoff(attempt, base_delay_ms, max_delay_ms)
            if deadline_s is not None:
                remaining = deadline_s - asyncio.get_event_loop().time()
                if remaining <= delay:
                    raise
            await sleep(delay)
    assert last_exc is not None  # unreachable; satisfies the type checker
    raise last_exc


def _backoff(attempt: int, base_ms: int, max_ms: int) -> float:
    """Full-jitter backoff: delay = random(0, min(max, base * 2^(attempt-1)))."""
    cap = min(max_ms, base_ms * (2 ** (attempt - 1)))
    return random.uniform(0, cap) / 1000.0
