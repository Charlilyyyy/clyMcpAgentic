"""Adversarial validation — bound payloads, scrub text, forbid unknowns."""

from __future__ import annotations

import pytest

from atlas_mcp.errors.framework import ValidationError
from atlas_mcp.tools.stub import StubPingTool
from atlas_mcp.validation.adversarial import (
    MAX_ARGUMENT_BYTES,
    assert_safe_identifier,
    bound_argument_tree,
    scrub_text,
)


def test_scrub_text_rejects_control_characters() -> None:
    with pytest.raises(ValidationError) as exc_info:
        scrub_text("hello\x00world", field="message")
    assert exc_info.value.code == "invalid_arguments"


def test_scrub_text_rejects_oversized_string() -> None:
    with pytest.raises(ValidationError):
        scrub_text("x" * 201, field="message", max_length=200)


def test_assert_safe_identifier_accepts_simple_names() -> None:
    assert assert_safe_identifier("orders_v1") == "orders_v1"


def test_assert_safe_identifier_rejects_injection_shaped_names() -> None:
    with pytest.raises(ValidationError):
        assert_safe_identifier("../etc/passwd")


def test_bound_argument_tree_rejects_oversized_payload() -> None:
    # Build a small object that still exceeds the byte budget when serialised.
    huge = {"blob": "y" * (MAX_ARGUMENT_BYTES + 1)}
    with pytest.raises(ValidationError) as exc_info:
        bound_argument_tree(huge)
    assert "bytes" in (exc_info.value.hint or "")


def test_bound_argument_tree_rejects_deep_nesting() -> None:
    node: dict = {}
    cursor = node
    for _ in range(12):
        cursor["child"] = {}
        cursor = cursor["child"]
    with pytest.raises(ValidationError) as exc_info:
        bound_argument_tree(node)
    assert "nest" in (exc_info.value.hint or "")


def test_ping_rejects_unknown_fields() -> None:
    tool = StubPingTool()
    with pytest.raises(ValidationError) as exc_info:
        tool.validate({"message": "ok", "evil": True})
    assert exc_info.value.code == "invalid_arguments"


def test_ping_rejects_invalid_enum_mode() -> None:
    tool = StubPingTool()
    with pytest.raises(ValidationError):
        tool.validate({"mode": "explode"})


def test_ping_accepts_bounded_valid_input() -> None:
    tool = StubPingTool()
    args = tool.validate({"message": "hello", "mode": "echo"})
    assert args.message == "hello"
    assert args.mode.value == "echo"
