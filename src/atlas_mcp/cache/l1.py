"""Component 9 — L1 in-process cache.

An async-safe, TTL-aware LRU. Sub-millisecond hits, invalidated by process
restart. Good for very hot keys (list_tools, catalog lookups) and as the fast
tier in front of the shared Redis L2 cache.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any


@dataclass
class L1Entry:
    value: Any
    expires_at: float


class L1Cache:
    """Async-safe in-process LRU with per-entry TTL."""

    def __init__(self, max_items: int, *, clock=time.monotonic) -> None:
        self.max_items = max_items
        self._store: OrderedDict[str, L1Entry] = OrderedDict()
        self._lock = asyncio.Lock()
        self._clock = clock

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry.expires_at < self._clock():
                self._store.pop(key, None)
                return None
            self._store.move_to_end(key)
            return entry.value

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        async with self._lock:
            self._store[key] = L1Entry(value=value, expires_at=self._clock() + ttl_seconds)
            self._store.move_to_end(key)
            while len(self._store) > self.max_items:
                self._store.popitem(last=False)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def delete_prefix(self, prefix: str) -> int:
        async with self._lock:
            matched = [k for k in self._store if k.startswith(prefix)]
            for k in matched:
                self._store.pop(k, None)
            return len(matched)

    def __len__(self) -> int:
        return len(self._store)
