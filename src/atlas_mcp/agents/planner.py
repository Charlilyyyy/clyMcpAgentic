"""Planner agent — turns a user question into a retrieval plan.

The planner never calls tools and never answers the question. It emits a
small JSON plan of information needs. It also deterministically extracts a
customer id from the question with a regex — the LLM is explicitly forbidden
from inventing one, so extraction happens in code, not in the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from atlas_mcp.agents.base import Agent, AgentRun
from atlas_mcp.agents.prompts import PLANNER_SYSTEM


@dataclass
class PlanStep:
    id: str
    description: str
    priority: int


@dataclass
class Plan:
    needs: list[PlanStep]
    customer_id_required: bool
    notes: str
    customer_id: str | None = None


_CUSTOMER_ID_RE = re.compile(r"\b(?:cust|customer|account|acct)[_-]?([A-Za-z0-9\-_]{3,64})\b", re.I)


class PlannerAgent(Agent):
    name = "planner"
    system_prompt = PLANNER_SYSTEM

    async def act(self, run: AgentRun, *, question: str) -> Plan:  # type: ignore[override]
        customer_id = self._extract_customer_id(question)
        data = await self._complete_json(
            run,
            messages=[
                {
                    "role": "user",
                    "content": f"Customer question:\n{question}\n\nReturn a retrieval plan as JSON.",
                }
            ],
            max_tokens=400,
        )
        return Plan(
            needs=[
                PlanStep(
                    id=str(n["id"]),
                    description=str(n["description"]),
                    priority=int(n.get("priority", 2)),
                )
                for n in data.get("needs", [])
            ],
            customer_id_required=bool(data.get("customer_id_required")),
            notes=str(data.get("notes", "")),
            customer_id=customer_id,
        )

    @staticmethod
    def _extract_customer_id(question: str) -> str | None:
        match = _CUSTOMER_ID_RE.search(question)
        return match.group(1) if match else None
