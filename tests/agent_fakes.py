"""Reusable fake LLM double for agent tests."""

from __future__ import annotations

import json
from typing import Any, Callable


class FakeLLM:
    """Returns queued responses; records the prompts it was asked to complete.

    Each queued item may be a plain string (wrapped as a text block) or a dict
    (used as the raw response). A ``responder`` callable can compute responses
    from the messages for dynamic multi-turn tests (e.g. the retriever loop).
    """

    def __init__(
        self,
        responses: list[Any] | None = None,
        responder: Callable[[str, list[dict]], Any] | None = None,
    ) -> None:
        self._responses = list(responses or [])
        self._responder = responder
        self.calls: list[tuple[str, list[dict]]] = []

    async def complete(self, system: str, messages: list[dict], max_tokens: int = 1024) -> dict:
        self.calls.append((system, messages))
        if self._responder is not None:
            item = self._responder(system, messages)
        elif self._responses:
            item = self._responses.pop(0)
        else:
            raise AssertionError("FakeLLM ran out of queued responses")
        return _as_response(item)


def _as_response(item: Any) -> dict:
    if isinstance(item, dict) and "content" in item:
        return item
    if isinstance(item, dict):
        text = json.dumps(item)
    else:
        text = str(item)
    return {
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 5, "output_tokens": 7},
    }
