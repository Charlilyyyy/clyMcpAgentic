"""Component 12 — Human-in-the-loop approval gate.

Destructive tool calls (writes, deletes, payments, emails) do not execute
immediately. The first attempt creates a *pending* approval record and returns
a structured ``pending_approval`` error to the agent. A human operator reviews
and approves/denies it; when the agent retries the identical call, the gate
finds the approval and lets it through.

The approval is keyed deterministically on ``(tenant, tool, args_hash)`` so the
agent does not have to echo an id back — retrying the same call is the resume
mechanism. This mirrors the confirmation step in Claude Code / Cursor / Copilot:
not ceremony, but the control surface that lets you ship agents safely.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from atlas_mcp.errors.framework import PolicyError, ToolError

APPROVAL_TTL_SECONDS = 3600


class ApprovalStore(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl_seconds: int) -> None: ...


class InMemoryApprovalStore:
    def __init__(self, *, clock=time.monotonic) -> None:
        self._store: dict[str, tuple[str, float]] = {}
        self._clock = clock

    async def get(self, key: str) -> str | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at < self._clock():
            self._store.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._store[key] = (value, self._clock() + ttl_seconds)


@dataclass
class PendingApproval:
    id: str
    tenant: str
    caller: str
    delegator: str | None
    tool: str
    args_hash: str
    created_at: str
    state: Literal["pending", "approved", "denied"] = "pending"
    approver: str | None = None


class PendingApprovalError(ToolError):
    """Signals the agent that human approval is required (not retryable as-is)."""

    def __init__(self, approval: PendingApproval) -> None:
        super().__init__(
            code="pending_approval",
            retryable=False,
            hint=(
                f"tool {approval.tool!r} requires human approval; "
                f"approval_id={approval.id}. Surface it to the user and wait."
            ),
            context={"approval_id": approval.id, "tool": approval.tool},
        )


def _args_hash(arguments: dict[str, Any]) -> str:
    payload = json.dumps(arguments, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()[:24]


class ApprovalGate:
    """Creates pending approvals and enforces their state on retry."""

    def __init__(self, store: ApprovalStore) -> None:
        self._store = store

    @staticmethod
    def approval_id(tenant: str, tool: str, arguments: dict[str, Any]) -> str:
        return f"atlas:approval:{tenant}:{tool}:{_args_hash(arguments)}"

    async def enforce(
        self,
        *,
        tenant: str,
        caller: str,
        delegator: str | None,
        tool: str,
        arguments: dict[str, Any],
    ) -> None:
        """Allow an approved call; otherwise create/keep a pending record and raise."""
        key = self.approval_id(tenant, tool, arguments)
        raw = await self._store.get(key)
        if raw is not None:
            approval = PendingApproval(**json.loads(raw))
            if approval.state == "approved":
                return
            if approval.state == "denied":
                raise PolicyError(
                    "approval_denied",
                    retryable=False,
                    hint=f"a human denied {tool!r} for this request",
                )
            raise PendingApprovalError(approval)

        approval = PendingApproval(
            id=key,
            tenant=tenant,
            caller=caller,
            delegator=delegator,
            tool=tool,
            args_hash=_args_hash(arguments),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        await self._store.set(key, json.dumps(asdict(approval)), APPROVAL_TTL_SECONDS)
        raise PendingApprovalError(approval)

    async def approve(self, approval_id: str, approver: str) -> PendingApproval:
        return await self._transition(approval_id, "approved", approver)

    async def deny(self, approval_id: str, approver: str) -> PendingApproval:
        return await self._transition(approval_id, "denied", approver)

    async def _transition(self, approval_id: str, state: str, approver: str) -> PendingApproval:
        raw = await self._store.get(approval_id)
        if raw is None:
            raise PolicyError(
                "approval_not_found",
                retryable=False,
                hint=f"approval {approval_id!r} does not exist or has expired",
            )
        approval = PendingApproval(**json.loads(raw))
        approval.state = state  # type: ignore[assignment]
        approval.approver = approver
        await self._store.set(approval_id, json.dumps(asdict(approval)), APPROVAL_TTL_SECONDS)
        return approval
