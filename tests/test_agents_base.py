"""Agent base — JSON parsing tolerance and token accounting."""

from __future__ import annotations

import pytest

from atlas_mcp.agents.base import AgentRun, first_text_block, parse_json_lenient


def test_parse_json_plain() -> None:
    assert parse_json_lenient('{"a": 1}') == {"a": 1}


def test_parse_json_with_markdown_fence() -> None:
    text = '```json\n{"a": 1}\n```'
    assert parse_json_lenient(text) == {"a": 1}


def test_parse_json_with_surrounding_prose() -> None:
    text = 'Sure! Here is the plan:\n{"needs": []}\nHope that helps.'
    assert parse_json_lenient(text) == {"needs": []}


def test_parse_json_raises_when_no_object() -> None:
    with pytest.raises(ValueError):
        parse_json_lenient("no json here")


def test_first_text_block_picks_text() -> None:
    resp = {"content": [{"type": "tool_use"}, {"type": "text", "text": "hi"}]}
    assert first_text_block(resp) == "hi"


def test_agent_run_defaults() -> None:
    run = AgentRun()
    assert run.tokens_in == 0
    assert run.tool_calls == 0
    assert len(run.run_id) > 0
