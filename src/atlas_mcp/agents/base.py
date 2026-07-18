"""Base classes for the agent layer.

Every agent in the copilot is a thin wrapper around one LLM call. The base
class handles token-budget accounting (so the orchestrator can enforce a
per-turn cap), JSON-mode parsing that tolerates stray prose, and a shared
``run_id`` so a whole conversation can be traced across all four agents.

The :class:`LLM` seam is deliberately tiny: swapping Anthropic for OpenAI,
Bedrock, or a fake test double means changing one class. Agents accept any
object implementing :class:`LLMProtocol`.
"""

from __future__ import annotations

import json
import os
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class AgentRun:
    """Per-turn accounting shared across all agents in one user interaction."""

    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    tokens_in: int = 0
    tokens_out: int = 0
    tool_calls: int = 0


class LLMProtocol(Protocol):
    async def complete(
        self, system: str, messages: list[dict], max_tokens: int = 1024
    ) -> dict:
        ...


class LLM:
    """Minimal async Anthropic Messages client — just enough for our agents."""

    def __init__(self, api_key: str | None = None, model: str = "claude-opus-4-8") -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model
        self.base_url = "https://api.anthropic.com/v1"

    async def complete(
        self, system: str, messages: list[dict], max_tokens: int = 1024
    ) -> dict:
        import httpx

        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{self.base_url}/messages", json=body, headers=headers)
            resp.raise_for_status()
            return resp.json()


class Agent(ABC):
    """Abstract base. Subclasses override :meth:`act`."""

    name: str = "agent"
    system_prompt: str = ""

    def __init__(self, llm: LLMProtocol) -> None:
        self.llm = llm

    @abstractmethod
    async def act(self, run: AgentRun, **inputs: Any) -> Any:
        ...

    async def _complete_json(
        self, run: AgentRun, messages: list[dict], max_tokens: int = 512
    ) -> dict:
        text = await self._complete_text(run, messages, max_tokens=max_tokens)
        return parse_json_lenient(text)

    async def _complete_text(
        self, run: AgentRun, messages: list[dict], max_tokens: int = 512
    ) -> str:
        resp = await self.llm.complete(self.system_prompt, messages, max_tokens=max_tokens)
        usage = resp.get("usage", {})
        run.tokens_in += usage.get("input_tokens", 0)
        run.tokens_out += usage.get("output_tokens", 0)
        return first_text_block(resp)


def first_text_block(response: dict) -> str:
    for block in response.get("content", []):
        if block.get("type") == "text":
            return block.get("text", "")
    return ""


def parse_json_lenient(text: str) -> dict:
    """Extract a JSON object from an LLM response even with stray prose/fences."""
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
        if text.startswith("json\n"):
            text = text[5:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in LLM output: {text[:200]!r}")
    return json.loads(text[start : end + 1])
