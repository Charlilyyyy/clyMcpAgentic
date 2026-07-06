"""Stub tool for transport-layer smoke tests."""

from __future__ import annotations

from pydantic import BaseModel, Field

from atlas_mcp.tools.base import Tool, ToolLevel, ToolMetadata


class PingInput(BaseModel):
    message: str = Field(default="ping", max_length=200)


class StubPingTool(Tool):
    """Returns a deterministic pong payload — used to verify list_tools / call_tool."""

    meta = ToolMetadata(
        name="server.ping",
        description="Health-check stub tool. Returns pong with the caller tenant.",
        level=ToolLevel.ATOMIC,
        scopes_required=(),
        cacheable=False,
        tags=("stub", "health"),
    )
    input_schema = PingInput

    async def run(self, tenant: str, args: PingInput) -> dict:
        return {
            "pong": True,
            "message": args.message,
            "tenant": tenant,
        }
