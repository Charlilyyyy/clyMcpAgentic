"""Critic verdicts and short-term memory buffer."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from agent_fakes import FakeLLM  # noqa: E402

from atlas_mcp.agents.base import AgentRun  # noqa: E402
from atlas_mcp.agents.critic import CriticAgent  # noqa: E402
from atlas_mcp.agents.memory import ShortTermMemory, Turn  # noqa: E402
from atlas_mcp.agents.retriever import Finding  # noqa: E402
from atlas_mcp.agents.synthesizer import Draft  # noqa: E402


class FakeRedis:
    """Minimal async Redis list double for STM tests."""

    def __init__(self):
        self.store: dict[str, list[str]] = {}
        self.expires: dict[str, int] = {}

    async def rpush(self, key, value):
        self.store.setdefault(key, []).append(value)

    async def ltrim(self, key, start, end):
        items = self.store.get(key, [])
        self.store[key] = items[start:] if end == -1 else items[start : end + 1]

    async def expire(self, key, seconds):
        self.expires[key] = seconds

    async def lrange(self, key, start, end):
        items = self.store.get(key, [])
        return items if end == -1 else items[start : end + 1]

    async def delete(self, key):
        self.store.pop(key, None)


@pytest.mark.asyncio
async def test_critic_approves_clean_draft() -> None:
    llm = FakeLLM(responses=[{"verdict": "approve", "issues": []}])
    verdict = await CriticAgent(llm).act(
        AgentRun(), question="q", findings=[Finding("orders", "shipped")],
        draft=Draft(text="shipped [S1]", citations=["[S1]"]),
    )
    assert verdict.approved is True
    assert verdict.issues == []


@pytest.mark.asyncio
async def test_critic_flags_unsupported_refund_promise() -> None:
    llm = FakeLLM(responses=[{
        "verdict": "revise",
        "issues": ["promises refund without approval"],
        "revision_hints": "remove the refund promise",
    }])
    verdict = await CriticAgent(llm).act(
        AgentRun(), question="refund?", findings=[],
        draft=Draft(text="You'll get a refund.", citations=[]),
    )
    assert verdict.approved is False
    assert "refund" in verdict.issues[0]
    assert verdict.revision_hints


@pytest.mark.asyncio
async def test_stm_appends_trims_and_expires() -> None:
    redis = FakeRedis()
    stm = ShortTermMemory(redis)
    for i in range(ShortTermMemory.MAX_TURNS + 5):
        await stm.append("sess1", Turn(role="user", content=f"msg{i}"))

    history = await stm.history("sess1")
    assert len(history) == ShortTermMemory.MAX_TURNS
    assert history[-1].content == "msg24"
    assert redis.expires[ShortTermMemory._key("sess1")] == ShortTermMemory.TTL_SECONDS

    await stm.clear("sess1")
    assert await stm.history("sess1") == []
