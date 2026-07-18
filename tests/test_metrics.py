"""Prometheus metrics registry and /metrics endpoint."""

from __future__ import annotations

from starlette.testclient import TestClient

from atlas_mcp.config import ServerSettings
from atlas_mcp.observability.metrics import MetricsRegistry
from atlas_mcp.reliability.circuit_breaker import State
from atlas_mcp.server import AtlasServer
from atlas_mcp.transport.http import build_http_app


def test_observe_call_records_counter_and_histogram() -> None:
    metrics = MetricsRegistry()
    metrics.observe_call("postgres.query", "ok", 0.05)
    metrics.observe_call("postgres.query", "error", 0.10)
    rendered = metrics.render().decode()
    assert 'atlas_tool_calls_total{status="ok",tool="postgres.query"} 1.0' in rendered
    assert 'atlas_tool_calls_total{status="error",tool="postgres.query"} 1.0' in rendered
    assert "atlas_tool_latency_seconds_bucket" in rendered


def test_cache_and_circuit_metrics() -> None:
    metrics = MetricsRegistry()
    metrics.observe_cache("semantic_search", hit=True)
    metrics.observe_cache("semantic_search", hit=False)
    metrics.observe_rate_limited("semantic_search")
    metrics.set_circuit_state("semantic_search", State.OPEN)
    rendered = metrics.render().decode()
    assert 'atlas_cache_hits_total{tool="semantic_search"} 1.0' in rendered
    assert 'atlas_cache_misses_total{tool="semantic_search"} 1.0' in rendered
    assert 'atlas_rate_limited_total{tool="semantic_search"} 1.0' in rendered
    assert 'atlas_circuit_state{tool="semantic_search"} 2.0' in rendered


def test_metrics_endpoint_is_public_and_renders() -> None:
    server = AtlasServer(ServerSettings())
    server.metrics.observe_call("server.ping", "ok", 0.01)
    with TestClient(build_http_app(server)) as client:
        response = client.get("/metrics")
    assert response.status_code == 200
    assert "atlas_tool_calls_total" in response.text
