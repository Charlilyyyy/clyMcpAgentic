"""Component 5 — Validation schemas.

The tool envelope is the one shape the dispatch pipeline speaks. Every MCP
request that reaches ``dispatch`` has been normalised into this dataclass,
so auth, rate limiting, caching, and execution all read from the same object.

Per-tool input validation lives on the :class:`Tool` subclasses themselves,
built on :class:`~atlas_mcp.validation.adversarial.StrictToolModel`.
This module only describes the envelope around the tool call.
"""

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
    # Threads through metrics, logs, and OTel spans for a single agent call.
    trace_id: str | None = None
    # Human who authorised the agent — survives the agent layer in audit logs.
    delegator: str | None = None
