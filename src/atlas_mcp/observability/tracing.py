"""Component 11 — Distributed tracing.

Every MCP tool call becomes an OpenTelemetry span carrying:

* ``atlas.tool`` — tool name
* ``atlas.tenant`` — tenant id
* ``atlas.cache`` — hit / miss / bypass
* ``atlas.circuit_state`` — CLOSED / HALF_OPEN / OPEN at call time
* ``atlas.error_code`` — the SERF code when the call failed

Spans export via OTLP to whatever collector ``ATLAS_OTEL_ENDPOINT`` points at
(a local OTel Collector → Jaeger in docker-compose). The point is not pretty
graphs; it is being able to open the exact span for a weird agent answer and
see the arguments, the upstream latency, and where it went wrong.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanProcessor

from atlas_mcp.config import ServerSettings

_INITIALISED = False


def build_tracer_provider(
    settings: ServerSettings, processor: SpanProcessor | None = None
) -> TracerProvider:
    """Create a provider. Tests inject an in-memory processor; prod uses OTLP."""
    resource = Resource.create({"service.name": settings.service_name, "service.version": "0.1.0"})
    provider = TracerProvider(resource=resource)
    if processor is None:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        processor = BatchSpanProcessor(
            OTLPSpanExporter(endpoint=settings.otel_endpoint, insecure=True)
        )
    provider.add_span_processor(processor)
    return provider


def init_tracing(settings: ServerSettings, processor: SpanProcessor | None = None) -> None:
    """Install a global tracer provider. Idempotent — safe to call twice."""
    global _INITIALISED
    if _INITIALISED:
        return
    trace.set_tracer_provider(build_tracer_provider(settings, processor))
    _INITIALISED = True


def get_tracer(provider: TracerProvider | None = None):
    if provider is not None:
        return provider.get_tracer("atlas_mcp")
    return trace.get_tracer("atlas_mcp")


def current_trace_id() -> str | None:
    span = trace.get_current_span()
    if span is None:
        return None
    ctx = span.get_span_context()
    if not ctx.is_valid:
        return None
    return f"{ctx.trace_id:032x}"


@contextmanager
def tool_span(tracer, tool: str, tenant: str) -> Iterator["trace.Span"]:
    """Open a span for a tool call, pre-populated with the standard attributes."""
    with tracer.start_as_current_span(f"tool.{tool}") as span:
        span.set_attribute("atlas.tool", tool)
        span.set_attribute("atlas.tenant", tenant)
        yield span


def annotate_cache(span, outcome: str) -> None:
    span.set_attribute("atlas.cache", outcome)


def annotate_circuit(span, state: str) -> None:
    span.set_attribute("atlas.circuit_state", state)


def annotate_error(span, error_code: str) -> None:
    span.set_attribute("atlas.error_code", error_code)
