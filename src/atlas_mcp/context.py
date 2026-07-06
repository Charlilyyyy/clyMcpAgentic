"""Request-scoped caller identity for the MCP dispatch pipeline.

HTTP middleware (auth, tenant) populates a :class:`RequestContext` per
request. stdio transport sets a development default before handling calls.
Downstream code reads the context through :func:`current_context` instead of
thread-local globals.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Iterable

from atlas_mcp.validation.schemas import ToolCallEnvelope

_DEFAULT_TENANT = "default"
_DEFAULT_SUBJECT = "dev:local"


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Identity attached to a single MCP request."""

    subject: str
    tenant: str
    scopes: tuple[str, ...] = ()
    delegator: str | None = None
    trace_id: str | None = None

    def has_scope(self, required: str) -> bool:
        return required in self.scopes or "tool:*:admin" in self.scopes

    def build_envelope(self, tool: str, arguments: dict) -> ToolCallEnvelope:
        return ToolCallEnvelope(
            tool=tool,
            arguments=arguments,
            tenant=self.tenant,
            caller=self.subject,
            trace_id=self.trace_id,
            delegator=self.delegator,
        )


_context: ContextVar[RequestContext | None] = ContextVar("atlas_request_context", default=None)


def dev_context(
    *,
    tenant: str = _DEFAULT_TENANT,
    subject: str = _DEFAULT_SUBJECT,
    scopes: Iterable[str] = (),
    delegator: str | None = None,
    trace_id: str | None = None,
) -> RequestContext:
    """Development default used by stdio transport before auth is wired."""
    return RequestContext(
        subject=subject,
        tenant=tenant,
        scopes=tuple(scopes),
        delegator=delegator,
        trace_id=trace_id,
    )


def set_context(ctx: RequestContext) -> Token:
    """Install context for the current async task. Returns a reset token."""
    return _context.set(ctx)


def reset_context(token: Token) -> None:
    _context.reset(token)


def current_context() -> RequestContext:
    """Return the active request context or a safe development default."""
    ctx = _context.get()
    if ctx is not None:
        return ctx
    return dev_context()


def clear_context() -> None:
    _context.set(None)
