"""OpenTelemetry tracing — span attributes captured per tool call."""

from __future__ import annotations

from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from atlas_mcp.config import ServerSettings
from atlas_mcp.observability.tracing import (
    annotate_cache,
    annotate_error,
    build_tracer_provider,
    get_tracer,
    tool_span,
)


def _provider_with_memory():
    exporter = InMemorySpanExporter()
    provider = build_tracer_provider(ServerSettings(), SimpleSpanProcessor(exporter))
    return provider, exporter


def test_tool_span_sets_standard_attributes() -> None:
    provider, exporter = _provider_with_memory()
    tracer = get_tracer(provider)

    with tool_span(tracer, "postgres.query", "acme") as span:
        annotate_cache(span, "miss")

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = spans[0].attributes
    assert attrs["atlas.tool"] == "postgres.query"
    assert attrs["atlas.tenant"] == "acme"
    assert attrs["atlas.cache"] == "miss"
    assert spans[0].name == "tool.postgres.query"


def test_error_annotation_recorded_on_span() -> None:
    provider, exporter = _provider_with_memory()
    tracer = get_tracer(provider)

    with tool_span(tracer, "s3.get_object", "globex") as span:
        annotate_error(span, "not_found")

    span = exporter.get_finished_spans()[0]
    assert span.attributes["atlas.error_code"] == "not_found"
