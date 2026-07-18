"""Synthesizer agent — drafts a customer reply from retrieved findings."""

from __future__ import annotations

import re
from dataclasses import dataclass

from atlas_mcp.agents.base import Agent, AgentRun
from atlas_mcp.agents.prompts import SYNTHESIZER_SYSTEM
from atlas_mcp.agents.retriever import Finding


@dataclass
class Draft:
    text: str
    citations: list[str]


class SynthesizerAgent(Agent):
    name = "synthesizer"
    system_prompt = SYNTHESIZER_SYSTEM

    async def act(  # type: ignore[override]
        self, run: AgentRun, *, question: str, findings: list[Finding]
    ) -> Draft:
        findings_block = "\n".join(
            f"[S{i}] {f.source}: {f.summary}" for i, f in enumerate(findings, start=1)
        )
        prompt = (
            f"Customer question:\n{question}\n\n"
            f"Findings:\n{findings_block}\n\n"
            "Write the draft reply. Use [S1], [S2]... anchors to cite findings."
        )
        text = await self._complete_text(
            run, messages=[{"role": "user", "content": prompt}], max_tokens=400
        )
        return Draft(text=text.strip(), citations=_extract_citations(text))


def _extract_citations(text: str) -> list[str]:
    return sorted(set(re.findall(r"\[S\d+\]", text)))
