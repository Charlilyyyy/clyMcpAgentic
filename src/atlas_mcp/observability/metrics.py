"""Component 11 — Prometheus metrics.

Each metric carries *just enough* labels to be useful without becoming a
cardinality bomb. We deliberately do NOT label by tenant or caller — those
explode the series count and belong in logs and traces, not gauges.

The server owns one :class:`MetricsRegistry`; the ``/metrics`` endpoint renders
whatever that instance has recorded. Tests build their own registry so counters
never leak between cases.
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from atlas_mcp.reliability.circuit_breaker import State

_STATE_VALUE = {State.CLOSED: 0, State.HALF_OPEN: 1, State.OPEN: 2}


class MetricsRegistry:
    """Holds all named Prometheus instruments used throughout the server."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()

        self.calls_total = Counter(
            "atlas_tool_calls_total",
            "Total tool invocations.",
            ["tool", "status"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "atlas_tool_latency_seconds",
            "End-to-end tool latency.",
            ["tool"],
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
            registry=self.registry,
        )
        self.cache_hits = Counter(
            "atlas_cache_hits_total", "Cache hits (L1 + L2).", ["tool"], registry=self.registry
        )
        self.cache_misses = Counter(
            "atlas_cache_misses_total",
            "Cache misses that reached the tool.",
            ["tool"],
            registry=self.registry,
        )
        self.rate_limited = Counter(
            "atlas_rate_limited_total",
            "Requests rejected by the rate limiter.",
            ["tool"],
            registry=self.registry,
        )
        self.circuit_state = Gauge(
            "atlas_circuit_state",
            "Circuit breaker state (0=closed, 1=half_open, 2=open).",
            ["tool"],
            registry=self.registry,
        )

    def observe_call(self, tool: str, status: str, duration_s: float) -> None:
        self.calls_total.labels(tool=tool, status=status).inc()
        self.latency.labels(tool=tool).observe(duration_s)

    def observe_cache(self, tool: str, hit: bool) -> None:
        (self.cache_hits if hit else self.cache_misses).labels(tool=tool).inc()

    def observe_rate_limited(self, tool: str) -> None:
        self.rate_limited.labels(tool=tool).inc()

    def set_circuit_state(self, tool: str, state: State) -> None:
        self.circuit_state.labels(tool=tool).set(_STATE_VALUE[state])

    def render(self) -> bytes:
        return generate_latest(self.registry)


CONTENT_TYPE = CONTENT_TYPE_LATEST
