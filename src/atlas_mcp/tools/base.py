"""Tool execution engine base types and three-level hierarchy metadata."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar

from pydantic import BaseModel

from atlas_mcp.errors.framework import ValidationError


class ToolLevel(str, Enum):
    ATOMIC = "atomic"
    COMPOSED = "composed"
    WORKFLOW = "workflow"


@dataclass
class ToolMetadata:
    """Descriptive metadata surfaced via list_tools and /.well-known."""

    name: str
    description: str
    level: ToolLevel
    scopes_required: tuple[str, ...] = ()
    destructive: bool = False
    cacheable: bool = True
    cache_ttl_seconds: int = 60
    timeout_ms: int = 10_000
    tags: tuple[str, ...] = ()


class Tool(ABC):
    """Base class. Subclass, declare a schema, implement ``run``."""

    meta: ClassVar[ToolMetadata]
    input_schema: ClassVar[type[BaseModel]]

    def validate(self, arguments: dict[str, Any]) -> BaseModel:
        """Validate tool arguments. Raises :class:`ValidationError` on failure."""
        try:
            return self.input_schema.model_validate(arguments)
        except Exception as exc:
            raise ValidationError(
                code="invalid_arguments",
                retryable=False,
                hint=_summarise_pydantic_error(exc),
                context={"tool": self.meta.name},
            ) from exc

    def cache_key(self, tenant: str, args: BaseModel) -> str:
        """Deterministic hash used by the cache layer."""
        payload = {"tool": self.meta.name, "tenant": tenant, "args": args.model_dump(mode="json")}
        blob = json.dumps(payload, sort_keys=True, default=str).encode()
        return f"atlas:{self.meta.name}:{hashlib.sha256(blob).hexdigest()[:24]}"

    @property
    def cacheable(self) -> bool:
        return self.meta.cacheable

    @property
    def cache_ttl_seconds(self) -> int:
        return self.meta.cache_ttl_seconds

    async def execute(self, tenant: str, args: BaseModel) -> dict[str, Any]:
        """Called by the dispatch pipeline after guards have passed."""
        return await self.run(tenant, args)

    @abstractmethod
    async def run(self, tenant: str, args: BaseModel) -> dict[str, Any]:
        """Perform the tool operation."""
        ...


def _summarise_pydantic_error(exc: Exception) -> str:
    if hasattr(exc, "errors"):
        errs = exc.errors()  # type: ignore[attr-defined]
        if errs:
            first = errs[0]
            loc = ".".join(str(part) for part in first.get("loc", []))
            return f"{loc}: {first.get('msg', 'invalid')}"
    return str(exc)
