"""Retrieval data types.

The :class:`RetrieverAgent` that populates these lives alongside the MCP
client (next commit); the dataclasses are separated out so the Synthesizer
and Critic can depend on the shape without pulling in the tool-calling loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Finding:
    source: str
    summary: str
    raw: Any = None


@dataclass
class RetrievalResult:
    findings: list[Finding] = field(default_factory=list)
    iterations_used: int = 0
    exceeded_budget: bool = False
