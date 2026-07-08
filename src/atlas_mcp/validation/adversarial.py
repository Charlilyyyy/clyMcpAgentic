"""Adversarial-input guards for agent-generated tool arguments.

Agents routinely emit unexpected keys, oversized strings, null bytes, and
control characters. Treat every payload as hostile until proven otherwise.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from atlas_mcp.errors.framework import ValidationError

# Identifiers used in resource paths, collection names, indexes, etc.
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,127}$")

# Reject C0 controls except tab / newline / carriage return.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Hard ceiling on JSON-serialisable argument tree size (bytes of utf-8 JSON).
MAX_ARGUMENT_BYTES = 64 * 1024
MAX_NESTING_DEPTH = 8
MAX_LIST_LENGTH = 1_000
MAX_OBJECT_KEYS = 100


class StrictToolModel(BaseModel):
    """Base for tool input schemas — forbid unknown fields by default."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def assert_safe_identifier(value: str, *, field: str = "name") -> str:
    """Require a conservative identifier; raise :class:`ValidationError` otherwise."""
    if not SAFE_IDENTIFIER.match(value):
        raise ValidationError(
            code="invalid_arguments",
            retryable=False,
            hint=f"{field}: must match {SAFE_IDENTIFIER.pattern}",
            context={"field": field, "value": value[:64]},
        )
    return value


def scrub_text(value: str, *, field: str = "text", max_length: int = 4_000) -> str:
    """Strip control characters and enforce a hard length bound."""
    if len(value) > max_length:
        raise ValidationError(
            code="invalid_arguments",
            retryable=False,
            hint=f"{field}: exceeds max length {max_length}",
            context={"field": field, "length": len(value)},
        )
    cleaned = _CONTROL_CHARS.sub("", value)
    if cleaned != value:
        raise ValidationError(
            code="invalid_arguments",
            retryable=False,
            hint=f"{field}: contains forbidden control characters",
            context={"field": field},
        )
    return cleaned


def bound_argument_tree(arguments: dict[str, Any]) -> dict[str, Any]:
    """Reject oversized or excessively nested argument payloads early."""
    import json

    try:
        blob = json.dumps(arguments, default=str).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            code="invalid_arguments",
            retryable=False,
            hint=f"arguments are not JSON-serialisable: {exc}",
        ) from exc

    if len(blob) > MAX_ARGUMENT_BYTES:
        raise ValidationError(
            code="invalid_arguments",
            retryable=False,
            hint=f"arguments exceed {MAX_ARGUMENT_BYTES} bytes",
            context={"bytes": len(blob)},
        )

    _check_depth(arguments, depth=0)
    return arguments


def _check_depth(node: Any, *, depth: int) -> None:
    if depth > MAX_NESTING_DEPTH:
        raise ValidationError(
            code="invalid_arguments",
            retryable=False,
            hint=f"arguments nest deeper than {MAX_NESTING_DEPTH} levels",
        )
    if isinstance(node, dict):
        if len(node) > MAX_OBJECT_KEYS:
            raise ValidationError(
                code="invalid_arguments",
                retryable=False,
                hint=f"object exceeds {MAX_OBJECT_KEYS} keys",
            )
        for value in node.values():
            _check_depth(value, depth=depth + 1)
    elif isinstance(node, list):
        if len(node) > MAX_LIST_LENGTH:
            raise ValidationError(
                code="invalid_arguments",
                retryable=False,
                hint=f"list exceeds {MAX_LIST_LENGTH} items",
            )
        for item in node:
            _check_depth(item, depth=depth + 1)


def text_field_validator(*fields: str, max_length: int = 4_000):
    """Factory for Pydantic field validators that scrub agent text."""

    @field_validator(*fields, mode="before")
    @classmethod
    def _scrub(cls, value: Any, info) -> Any:  # type: ignore[no-untyped-def]
        if value is None:
            return value
        if not isinstance(value, str):
            raise ValueError("expected a string")
        return scrub_text(value, field=info.field_name or "text", max_length=max_length)

    return _scrub
