"""Planner and Synthesizer agents."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from agent_fakes import FakeLLM  # noqa: E402

from atlas_mcp.agents.base import AgentRun  # noqa: E402
from atlas_mcp.agents.planner import PlannerAgent  # noqa: E402
from atlas_mcp.agents.retriever import Finding  # noqa: E402
from atlas_mcp.agents.synthesizer import SynthesizerAgent  # noqa: E402


@pytest.mark.asyncio
async def test_planner_extracts_customer_id_in_code() -> None:
    llm = FakeLLM(
        responses=[
            {
                "needs": [{"id": "n1", "description": "orders", "priority": 1}],
                "customer_id_required": False,
                "notes": "",
            }
        ]
    )
    plan = await PlannerAgent(llm).act(AgentRun(), question="Refund for CUST-1234 please?")
    assert plan.customer_id == "1234"
    assert plan.needs[0].id == "n1"
    assert plan.customer_id_required is False


@pytest.mark.asyncio
async def test_planner_flags_missing_customer_id() -> None:
    llm = FakeLLM(responses=[{"needs": [], "customer_id_required": True, "notes": "need id"}])
    plan = await PlannerAgent(llm).act(AgentRun(), question="Where is my order?")
    assert plan.customer_id is None
    assert plan.customer_id_required is True


@pytest.mark.asyncio
async def test_planner_tolerates_prose_wrapped_json() -> None:
    llm = FakeLLM(responses=['Here you go:\n{"needs": [], "customer_id_required": false}'])
    plan = await PlannerAgent(llm).act(AgentRun(), question="hello")
    assert plan.needs == []


@pytest.mark.asyncio
async def test_synthesizer_extracts_citations_and_counts_tokens() -> None:
    llm = FakeLLM(responses=["Your order shipped [S1] and a doc explains returns [S2]."])
    run = AgentRun()
    findings = [Finding("orders", "shipped"), Finding("docs", "returns policy")]
    draft = await SynthesizerAgent(llm).act(run, question="status?", findings=findings)
    assert draft.citations == ["[S1]", "[S2]"]
    assert run.tokens_out > 0
