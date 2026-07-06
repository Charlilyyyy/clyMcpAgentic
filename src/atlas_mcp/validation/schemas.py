"""Normalised shapes for the dispatch pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolCallEnvelope:
    """Normalised shape of an incoming MCP tool call."""

    tool: str
    arguments: dict[str, Any]
    tenant: str
    caller: str
    trace_id: str | None = None
    delegator: str | None = None
