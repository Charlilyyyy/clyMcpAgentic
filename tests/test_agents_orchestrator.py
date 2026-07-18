"""End-to-end orchestration: planner → retriever → synthesizer → critic."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from agent_fakes import FakeLLM  # noqa: E402

from atlas_mcp.agents.mcp_client import ToolResult  # noqa: E402
from atlas_mcp.agents.orchestrator import SupportCopilot  # noqa: E402
from atlas_mcp.agents.prompts import (  # noqa: E402
    CRITIC_SYSTEM,
    PLANNER_SYSTEM,
    RETRIEVER_SYSTEM,
    SYNTHESIZER_SYSTEM,
)


class FakeMCPClient:
    def __init__(self):
        self.called: list[tuple[str, dict]] = []

    async def list_tools(self):
        return [{"name": "customer.build_context", "description": "fan-out"}]

    async def call_tool(self, name, arguments):
        self.called.append((name, arguments))
        return ToolResult(ok=True, value={"customer": "Acme", "orders": [{"id": "o1"}]})


def _router(script: dict[str, list]):
    """Return a FakeLLM responder that dispatches by system prompt."""
    queues = {k: list(v) for k, v in script.items()}

    def responder(system: str, messages: list[dict]):
        if system == PLANNER_SYSTEM:
            return queues["planner"].pop(0)
        if system == RETRIEVER_SYSTEM:
            return queues["retriever"].pop(0)
        if system == SYNTHESIZER_SYSTEM:
            return queues["synthesizer"].pop(0)
        if system == CRITIC_SYSTEM:
            return queues["critic"].pop(0)
        raise AssertionError(f"unexpected system prompt: {system[:40]}")

    return responder


@pytest.mark.asyncio
async def test_happy_path_approved_first_try() -> None:
    responder = _router(
        {
            "planner": [
                {
                    "needs": [{"id": "n1", "description": "orders", "priority": 1}],
                    "customer_id_required": True,
                    "notes": "",
                }
            ],
            "retriever": [
                {"tool": "customer.build_context", "arguments": {"customer_id": "1234"}},
                {
                    "done": True,
                    "findings": [{"source": "customer.build_context", "summary": "shipped"}],
                },
            ],
            "synthesizer": ["Your order shipped [S1]."],
            "critic": [{"verdict": "approve", "issues": []}],
        }
    )
    copilot = SupportCopilot(FakeMCPClient(), llm=FakeLLM(responder=responder))
    resp = await copilot.answer("Where is order for CUST-1234?")

    assert resp.approved is True
    assert resp.citations == ["[S1]"]
    assert resp.tool_calls == 1
    assert resp.retrieval.findings


@pytest.mark.asyncio
async def test_missing_customer_id_short_circuits_before_retrieval() -> None:
    responder = _router(
        {
            "planner": [{"needs": [], "customer_id_required": True, "notes": "need id"}],
            "retriever": [],
            "synthesizer": [],
            "critic": [],
        }
    )
    mcp = FakeMCPClient()
    copilot = SupportCopilot(mcp, llm=FakeLLM(responder=responder))
    resp = await copilot.answer("Where is my order?")

    assert resp.approved is True
    assert "customer id" in resp.draft.lower()
    assert mcp.called == []  # never retrieved


@pytest.mark.asyncio
async def test_critic_revision_loop_runs_once() -> None:
    responder = _router(
        {
            "planner": [
                {
                    "needs": [{"id": "n1", "description": "x", "priority": 1}],
                    "customer_id_required": False,
                    "notes": "",
                }
            ],
            "retriever": [
                {"done": True, "findings": [{"source": "hybrid_search", "summary": "policy doc"}]},
            ],
            "synthesizer": ["You'll get a refund.", "This may require approval [S1]."],
            "critic": [
                {
                    "verdict": "revise",
                    "issues": ["unapproved refund"],
                    "revision_hints": "add approval note",
                },
                {"verdict": "approve", "issues": []},
            ],
        }
    )
    copilot = SupportCopilot(FakeMCPClient(), llm=FakeLLM(responder=responder))
    resp = await copilot.answer("Refund for my order please")

    assert resp.approved is True
    assert "approval" in resp.draft.lower()
    d = resp.to_dict()
    assert d["critic"]["approved"] is True and d["retrieval"]["findings_count"] == 1


@pytest.mark.asyncio
async def test_no_findings_short_circuits() -> None:
    responder = _router(
        {
            "planner": [{"needs": [], "customer_id_required": False, "notes": ""}],
            "retriever": [{"done": True, "findings": []}],
            "synthesizer": [],
            "critic": [],
        }
    )
    copilot = SupportCopilot(FakeMCPClient(), llm=FakeLLM(responder=responder))
    resp = await copilot.answer("anything?")
    assert "relevant information" in resp.draft.lower()
    assert json.loads(json.dumps(resp.to_dict()))  # fully serialisable
