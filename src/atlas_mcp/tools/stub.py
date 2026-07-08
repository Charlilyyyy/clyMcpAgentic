"""Stub tool for transport-layer smoke tests."""

from __future__ import annotations

from enum import Enum

from pydantic import Field, field_validator

from atlas_mcp.tools.base import Tool, ToolLevel, ToolMetadata
from atlas_mcp.validation.adversarial import StrictToolModel, scrub_text


class PingMode(str, Enum):
    ECHO = "echo"
    SILENT = "silent"


class PingInput(StrictToolModel):
    message: str = Field(default="ping", max_length=200)
    mode: PingMode = Field(default=PingMode.ECHO)

    @field_validator("message")
    @classmethod
    def message_must_be_clean(cls, value: str) -> str:
        return scrub_text(value, field="message", max_length=200)


class PingOutput(StrictToolModel):
    pong: bool
    message: str
    tenant: str
    mode: PingMode


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
        if args.mode == PingMode.SILENT:
            return PingOutput(
                pong=True,
                message="",
                tenant=tenant,
                mode=args.mode,
            ).model_dump(mode="json")
        return PingOutput(
            pong=True,
            message=args.message,
            tenant=tenant,
            mode=args.mode,
        ).model_dump(mode="json")
