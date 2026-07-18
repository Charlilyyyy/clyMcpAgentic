"""Retriever tool-calling loop and the thin MCP client."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from agent_fakes import FakeLLM  # noqa: E402

from atlas_mcp.agents.base import AgentRun  # noqa: E402
from atlas_mcp.agents.mcp_client import AtlasMCPClient, ToolResult  # noqa: E402
from atlas_mcp.agents.planner import Plan, PlanStep  # noqa: E402
from atlas_mcp.agents.retriever import RetrieverAgent  # noqa: E402


class FakeMCPClient:
    def __init__(self, tools=None, results=None):
        self._tools = tools or [{"name": "customer.build_context", "description": "fan-out"}]
        self._results = results or {}
        self.called: list[tuple[str, dict]] = []

    async def list_tools(self):
        return self._tools

    async def call_tool(self, name, arguments):
        self.called.append((name, arguments))
        return self._results.get(name, ToolResult(ok=True, value={"rows": []}))


def _plan():
    return Plan(
        needs=[PlanStep("n1", "orders", 1)],
        customer_id_required=False,
        notes="",
        customer_id="1234",
    )


@pytest.mark.asyncio
async def test_retriever_calls_tool_then_finishes() -> None:
    responder_calls = {"i": 0}

    def responder(system, messages):
        responder_calls["i"] += 1
        if responder_calls["i"] == 1:
            return {"tool": "customer.build_context", "arguments": {"customer_id": "1234"}}
        return {"done": True, "findings": [{"source": "customer.build_context", "summary": "ok"}]}

    llm = FakeLLM(responder=responder)
    mcp = FakeMCPClient(
        results={"customer.build_context": ToolResult(ok=True, value={"name": "Acme"})}
    )
    run = AgentRun()
    result = await RetrieverAgent(llm, mcp, max_iterations=6).act(run, plan=_plan(), question="q")

    assert mcp.called == [("customer.build_context", {"customer_id": "1234"})]
    assert run.tool_calls == 1
    assert any(f.source == "customer.build_context" for f in result.findings)
    assert result.exceeded_budget is False


@pytest.mark.asyncio
async def test_retriever_blocks_disallowed_tool() -> None:
    def responder(system, messages):
        if "s3.put" in json.dumps(messages):
            return {"done": True, "findings": []}
        return {"tool": "s3.put", "arguments": {}}

    llm = FakeLLM(responder=responder)
    mcp = FakeMCPClient()
    result = await RetrieverAgent(llm, mcp, max_iterations=4).act(
        AgentRun(), plan=_plan(), question="q"
    )
    assert mcp.called == []  # disallowed tool never dispatched
    assert result.exceeded_budget is False


@pytest.mark.asyncio
async def test_retriever_bounded_loop_marks_budget_exceeded() -> None:
    llm = FakeLLM(responder=lambda s, m: {"tool": "semantic_search", "arguments": {}})
    mcp = FakeMCPClient(tools=[{"name": "semantic_search", "description": "d"}])
    result = await RetrieverAgent(llm, mcp, max_iterations=3).act(
        AgentRun(), plan=_plan(), question="q"
    )
    assert result.iterations_used == 3
    assert result.exceeded_budget is True


@pytest.mark.asyncio
async def test_mcp_client_parses_success_and_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        args = body["params"].get("arguments", {})
        if args.get("fail"):
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "error": {
                        "code": -32001,
                        "message": "boom",
                        "data": {"code": "upstream_error", "retryable": True, "hint": "try later"},
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {"content": [{"type": "text", "text": json.dumps({"rows": [1]})}]},
            },
        )

    client = AtlasMCPClient("http://atlas", "tok", tenant="acme")
    client._client = httpx.AsyncClient(
        base_url="http://atlas", transport=httpx.MockTransport(handler)
    )
    ok = await client.call_tool("postgres.query", {"sql": "select 1"})
    assert ok.ok and ok.value == {"rows": [1]}

    bad = await client.call_tool("postgres.query", {"fail": True})
    assert bad.ok is False and bad.error_code == "upstream_error" and bad.retryable is True
    await client._client.aclose()
