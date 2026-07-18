"""Component 11 (audit leg) — Structured audit log.

Audit logs differ from application logs: they are the regulator-readable paper
trail of "who called what, when, for which tenant, with what outcome". They
should never be lost, rarely be verbose, and always be parseable.

We emit newline-delimited JSON — one line per tool call. Arguments are never
written verbatim (they may hold PII); only a sha256 ``args_hash`` is stored so
calls can be correlated without leaking payloads. In production this file is
shipped to a SIEM (Splunk, Datadog, Chronicle) that owns durability/retention.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _hash_args(arguments: dict[str, Any]) -> str:
    payload = json.dumps(arguments, sort_keys=True, default=str).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class AuditLogger:
    """Appends one JSON line per tool call to a durable file sink."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._dir_ready = False

    def _ensure_dir(self) -> None:
        if not self._dir_ready:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._dir_ready = True

    def record(
        self,
        *,
        trace_id: str | None,
        tenant: str,
        caller: str,
        delegator: str | None,
        tool: str,
        arguments: dict[str, Any],
        duration_ms: float,
        status: str,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "trace_id": trace_id,
            "tenant": tenant,
            "caller": caller,
            "delegator": delegator,
            "tool": tool,
            "args_hash": _hash_args(arguments),
            "duration_ms": round(duration_ms, 3),
            "status": status,
            "error_code": error_code,
        }
        self._ensure_dir()
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, default=str) + "\n")
        return event

    def read_all(self) -> list[dict[str, Any]]:
        """Load every recorded event (test/inspection helper)."""
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def query(
        self, *, tenant: str | None = None, tool: str | None = None
    ) -> list[dict[str, Any]]:
        """Answer 'who called what tool for tenant X?' style questions."""
        events = self.read_all()
        if tenant is not None:
            events = [e for e in events if e["tenant"] == tenant]
        if tool is not None:
            events = [e for e in events if e["tool"] == tool]
        return events
