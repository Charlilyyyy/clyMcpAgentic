"""Approval gate — destructive tools blocked until a human approves."""

from __future__ import annotations

import contextlib
from typing import ClassVar

import pytest

from atlas_mcp.config import ServerSettings
from atlas_mcp.context import RequestContext, dev_context, reset_context, set_context
from atlas_mcp.errors.framework import PolicyError
from atlas_mcp.governance.approval import (
    ApprovalGate,
    InMemoryApprovalStore,
    PendingApprovalError,
)
from atlas_mcp.server import AtlasServer
from atlas_mcp.tools.base import Tool, ToolLevel, ToolMetadata
from atlas_mcp.validation.adversarial import StrictToolModel


class _WriteArgs(StrictToolModel):
    key: str


class _WriteTool(Tool):
    meta: ClassVar[ToolMetadata] = ToolMetadata(
        name="demo.write",
        description="A destructive write tool.",
        level=ToolLevel.ATOMIC,
        scopes_required=(),
        destructive=True,
        cacheable=False,
    )
    input_schema: ClassVar[type[StrictToolModel]] = _WriteArgs

    def __init__(self) -> None:
        self.executed = 0

    async def run(self, tenant, args):  # type: ignore[override]
        self.executed += 1
        return {"written": args.key}


@pytest.mark.asyncio
async def test_gate_creates_pending_then_allows_after_approval() -> None:
    gate = ApprovalGate(InMemoryApprovalStore())
    kwargs = dict(tenant="acme", caller="agent", delegator="human", tool="demo.write",
                  arguments={"key": "a"})

    with pytest.raises(PendingApprovalError) as exc_info:
        await gate.enforce(**kwargs)
    approval_id = exc_info.value.context["approval_id"]

    # Still pending on retry.
    with pytest.raises(PendingApprovalError):
        await gate.enforce(**kwargs)

    await gate.approve(approval_id, approver="ops:jane")
    # Now the identical call passes through.
    await gate.enforce(**kwargs)


@pytest.mark.asyncio
async def test_gate_denied_raises_policy_error() -> None:
    gate = ApprovalGate(InMemoryApprovalStore())
    kwargs = dict(tenant="acme", caller="agent", delegator=None, tool="demo.write",
                  arguments={"key": "a"})
    with pytest.raises(PendingApprovalError) as exc_info:
        await gate.enforce(**kwargs)
    await gate.deny(exc_info.value.context["approval_id"], approver="ops:bob")
    with pytest.raises(PolicyError) as exc_info2:
        await gate.enforce(**kwargs)
    assert exc_info2.value.code == "approval_denied"


def _ctx() -> RequestContext:
    return dev_context(tenant="acme", subject="agent", scopes=("tool:*:admin",))


@contextlib.contextmanager
def _active_context():
    token = set_context(_ctx())
    try:
        yield
    finally:
        reset_context(token)


@pytest.mark.asyncio
async def test_dispatch_blocks_destructive_tool_until_approved() -> None:
    tool = _WriteTool()
    server = AtlasServer(ServerSettings())
    server.registry.register(tool)
    server.policy.check = lambda **kwargs: None  # type: ignore[method-assign]

    with _active_context():
        envelope = _ctx().build_envelope("demo.write", {"key": "report.txt"})

        with pytest.raises(PendingApprovalError) as exc_info:
            await server.dispatch(envelope)
        assert tool.executed == 0

        await server.approvals.approve(exc_info.value.context["approval_id"], "ops:jane")
        result = await server.dispatch(envelope)
        assert result == {"written": "report.txt"}
        assert tool.executed == 1
