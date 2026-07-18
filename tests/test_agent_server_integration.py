"""Agent ↔ server integration: the orchestrator driven over the real MCP stack.

Unlike the unit tests (which fake the MCP client), this test runs the
Retriever's tool calls through the *actual* server pipeline: streamable
HTTP transport → bearer auth → policy engine → validation → dispatch →
the ``customer.build_context`` workflow tool. Only the LLM is faked; every
other layer is real.

The server runs deny-by-default policy with no rule for the workflow tool,
so the call is rejected server-side even though the client allow-list
permits it — the "belt and suspenders" property. We assert the full
Planner → Retriever → Synthesizer → Critic loop completes, the SERF error
is surfaced to the agent as a structured finding, and the run still
produces a critic-approved draft rather than crashing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

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
from atlas_mcp.config import ServerSettings  # noqa: E402
from atlas_mcp.server import AtlasServer  # noqa: E402
from atlas_mcp.transport.http import build_http_app  # noqa: E402


class SessionMCPAdapter:
    """Presents the AtlasMCPClient surface the Retriever expects, backed by a
    live MCP :class:`ClientSession`."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def list_tools(self) -> list[dict]:
        resp = await self._session.list_tools()
        return [{"name": t.name, "description": t.description or ""} for t in resp.tools]

    async def call_tool(self, name: str, arguments: dict) -> ToolResult:
        result = await self._session.call_tool(name, arguments)
        if result.isError:
            err = result.structuredContent or {}
            hint = err.get("hint")
            if not hint and result.content and getattr(result.content[0], "text", None):
                hint = result.content[0].text
            return ToolResult(
                ok=False,
                error_code=err.get("code", "tool_error"),
                retryable=bool(err.get("retryable")),
                hint=hint,
            )
        value = result.structuredContent
        if value is None and result.content and getattr(result.content[0], "text", None):
            try:
                value = json.loads(result.content[0].text)
            except json.JSONDecodeError:
                value = result.content[0].text
        return ToolResult(ok=True, value=value)


def _router(script: dict[str, list]):
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
async def test_orchestrator_runs_end_to_end_over_real_server() -> None:
    server = AtlasServer(ServerSettings(auth_dev_token="test-token"))
    app = build_http_app(server)

    responder = _router(
        {
            "planner": [
                {
                    "needs": [{"id": "n1", "description": "customer context", "priority": 1}],
                    "customer_id_required": True,
                    "notes": "",
                }
            ],
            "retriever": [
                {
                    "tool": "customer.build_context",
                    "arguments": {
                        "customer_id": "1234",
                        "question": "Where is my order?",
                    },
                },
                {
                    "done": True,
                    "findings": [{"source": "customer.build_context", "summary": "policy denied"}],
                },
            ],
            "synthesizer": ["I could not retrieve that data [S1]; escalating to a human."],
            "critic": [{"verdict": "approve", "issues": []}],
        }
    )

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            follow_redirects=True,
            headers={"Authorization": "Bearer test-token"},
        ) as http_client:
            async with streamable_http_client("http://test/mcp/", http_client=http_client) as (
                read_stream,
                write_stream,
                _sid,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    adapter = SessionMCPAdapter(session)
                    copilot = SupportCopilot(adapter, llm=FakeLLM(responder=responder))

                    resp = await copilot.answer("Where is order for CUST-1234?")

    assert resp.approved is True
    assert resp.tool_calls == 1  # the retriever did attempt the real call
    assert resp.citations == ["[S1]"]
    assert resp.retrieval.findings
    # Server-side policy denied the call; the SERF error reached the agent as a
    # structured, non-crashing finding (belt-and-suspenders enforcement).
    denial = resp.retrieval.findings[0]
    assert denial.source == "customer.build_context"
    assert "not_authorized" in denial.summary


@pytest.mark.asyncio
async def test_disallowed_tool_is_never_dispatched_to_server() -> None:
    """Client-side allow-list blocks a destructive tool before it hits the wire."""
    server = AtlasServer(ServerSettings(auth_dev_token="test-token"))
    app = build_http_app(server)

    responder = _router(
        {
            "planner": [{"needs": [], "customer_id_required": False, "notes": ""}],
            "retriever": [
                {"tool": "s3.put", "arguments": {"key": "x", "body": "y"}},
                {"done": True, "findings": [{"source": "none", "summary": "blocked"}]},
            ],
            "synthesizer": ["Unable to complete that write [S1]."],
            "critic": [{"verdict": "approve", "issues": []}],
        }
    )

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            follow_redirects=True,
            headers={"Authorization": "Bearer test-token"},
        ) as http_client:
            async with streamable_http_client("http://test/mcp/", http_client=http_client) as (
                read_stream,
                write_stream,
                _sid,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    adapter = SessionMCPAdapter(session)
                    copilot = SupportCopilot(adapter, llm=FakeLLM(responder=responder))
                    resp = await copilot.answer("Please delete my data")

    assert resp.tool_calls == 0  # s3.put never dispatched
    assert resp.approved is True
