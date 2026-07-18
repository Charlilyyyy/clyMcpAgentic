"""Workflow tool — ``customer.build_context`` (Level 3).

Fans out to multiple atomic/composed tools concurrently, aggregates the
results, and returns a single coherent "customer context" document. This is
the tool a support-copilot agent calls first when a new ticket arrives — it
captures the deterministic "look up everything relevant" plan so the LLM does
not reconstruct it on every ticket.

Sub-tasks run with ``return_exceptions=True``: a partial context is more
useful to the agent than a hard failure when one backend is down.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

from pydantic import Field

from atlas_mcp.errors.framework import ToolError
from atlas_mcp.tools.atomic.elasticsearch import ElasticsearchSearchInput, ElasticsearchSearchTool
from atlas_mcp.tools.atomic.postgres import PostgresQueryInput, PostgresQueryTool
from atlas_mcp.tools.base import Tool, ToolLevel, ToolMetadata
from atlas_mcp.tools.composed.semantic_search import SemanticSearchInput, SemanticSearchTool
from atlas_mcp.validation.adversarial import StrictToolModel


class CustomerContextInput(StrictToolModel):
    customer_id: str = Field(..., pattern=r"^[A-Za-z0-9\-_]{1,64}$")
    question: str = Field(..., min_length=1, max_length=2000)
    include_orders: bool = True
    include_tickets: bool = True
    include_docs: bool = True
    recent_orders_limit: int = Field(5, ge=1, le=20)


class CustomerContextTool(Tool):
    """Fan-out workflow that builds the canonical support context."""

    meta: ClassVar[ToolMetadata] = ToolMetadata(
        name="customer.build_context",
        description=(
            "Aggregate a customer's recent orders, open tickets, and relevant "
            "documentation passages for the given question. Returns a single "
            "structured context object."
        ),
        level=ToolLevel.WORKFLOW,
        scopes_required=(
            "tool:postgres:read",
            "tool:elasticsearch:read",
            "tool:vector:read",
        ),
        cacheable=True,
        cache_ttl_seconds=30,
        timeout_ms=15_000,
        tags=("support", "context", "workflow"),
    )
    input_schema: ClassVar[type[StrictToolModel]] = CustomerContextInput

    def __init__(
        self,
        postgres_tool: PostgresQueryTool | None = None,
        es_tool: ElasticsearchSearchTool | None = None,
        semantic_tool: SemanticSearchTool | None = None,
    ) -> None:
        self.postgres = postgres_tool or PostgresQueryTool()
        self.es = es_tool or ElasticsearchSearchTool()
        self.semantic = semantic_tool or SemanticSearchTool()

    async def run(self, tenant: str, args: CustomerContextInput) -> dict:  # type: ignore[override]
        tasks: dict[str, asyncio.Task] = {
            "profile": asyncio.create_task(self._profile(tenant, args.customer_id)),
        }
        if args.include_orders:
            tasks["orders"] = asyncio.create_task(
                self._recent_orders(tenant, args.customer_id, args.recent_orders_limit)
            )
        if args.include_tickets:
            tasks["tickets"] = asyncio.create_task(self._open_tickets(tenant, args.customer_id))
        if args.include_docs:
            tasks["docs"] = asyncio.create_task(self._relevant_docs(tenant, args.question))

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        keyed = dict(zip(tasks.keys(), results))

        context: dict[str, Any] = {"customer_id": args.customer_id, "question": args.question}
        errors: dict[str, str] = {}
        for key, value in keyed.items():
            if isinstance(value, ToolError):
                errors[key] = value.code
            elif isinstance(value, BaseException):
                errors[key] = f"internal_error: {type(value).__name__}"
            else:
                context[key] = value
        if errors:
            context["partial_errors"] = errors
        return context

    async def _profile(self, tenant: str, customer_id: str) -> dict:
        result = await self.postgres.run(
            tenant,
            PostgresQueryInput(
                sql="SELECT id, name, email, tier, created_at FROM customers WHERE id = $1",
                params=[customer_id],
                max_rows=1,
            ),
        )
        rows = result["rows"]
        return rows[0] if rows else {}

    async def _recent_orders(self, tenant: str, customer_id: str, limit: int) -> list[dict]:
        result = await self.postgres.run(
            tenant,
            PostgresQueryInput(
                sql=(
                    "SELECT id, status, total_cents, currency, created_at "
                    "FROM orders WHERE customer_id = $1 ORDER BY created_at DESC"
                ),
                params=[customer_id],
                max_rows=limit,
            ),
        )
        return result["rows"]

    async def _open_tickets(self, tenant: str, customer_id: str) -> list[dict]:
        result = await self.es.run(
            tenant,
            ElasticsearchSearchInput(
                index="tickets",
                query={
                    "bool": {
                        "must": [
                            {"term": {"customer_id": customer_id}},
                            {"terms": {"status": ["open", "pending"]}},
                        ]
                    }
                },
                size=10,
                fields=["subject", "status", "priority", "created_at"],
            ),
        )
        return [h["source"] | {"id": h["id"]} for h in result["hits"]]

    async def _relevant_docs(self, tenant: str, question: str) -> list[dict]:
        result = await self.semantic.run(
            tenant,
            SemanticSearchInput(
                query=question, collection="support_docs", top_k=3, hydrate_from_postgres=False
            ),
        )
        return result["results"]
