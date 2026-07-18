"""Component 7 — Reliability metrics.

A lightweight in-process view of breaker health and retry activity. Phase 10
exports these to Prometheus; here we keep a dependency-free snapshot that the
server, tests, and a future ``/metrics`` scraper can all read.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from atlas_mcp.reliability.circuit_breaker import CircuitBreakerRegistry


@dataclass
class ReliabilityMetrics:
    """Mutable counters updated by the dispatch pipeline."""

    retries_attempted: int = 0
    retries_exhausted: int = 0
    breaker_short_circuits: int = 0
    per_tool_calls: dict[str, int] = field(default_factory=dict)

    def record_call(self, tool: str) -> None:
        self.per_tool_calls[tool] = self.per_tool_calls.get(tool, 0) + 1

    def record_retry(self) -> None:
        self.retries_attempted += 1

    def record_retry_exhausted(self) -> None:
        self.retries_exhausted += 1

    def record_short_circuit(self) -> None:
        self.breaker_short_circuits += 1

    def snapshot(self, breakers: CircuitBreakerRegistry) -> dict:
        return {
            "retries_attempted": self.retries_attempted,
            "retries_exhausted": self.retries_exhausted,
            "breaker_short_circuits": self.breaker_short_circuits,
            "per_tool_calls": dict(self.per_tool_calls),
            "breakers": {
                name: {"state": b.state.value, "trips": b.trips}
                for name, b in breakers._breakers.items()  # noqa: SLF001 — internal view
            },
        }
