"""Per-tenant outbound HTTP allowlist for SSRF protection."""

from __future__ import annotations

from pathlib import Path

import yaml

from atlas_mcp.errors.framework import PolicyError


class HttpAllowlist:
    """Hostnames permitted for outbound HTTPS requests per tenant."""

    def __init__(self, rules: dict[str, list[str]]):
        self._rules = rules

    @classmethod
    def from_file(cls, path: str | Path) -> "HttpAllowlist":
        file_path = Path(path)
        if not file_path.exists():
            return cls({})
        data = yaml.safe_load(file_path.read_text()) or {}
        return cls(data)

    def allowed(self, tenant: str, host: str) -> bool:
        patterns = (
            *(self._rules.get(tenant, []) or []),
            *(self._rules.get("*", []) or []),
        )
        for pattern in patterns:
            if pattern == host:
                return True
            if pattern.startswith("*.") and host.endswith(pattern[1:]):
                return True
        return False

    def check(self, tenant: str, host: str) -> None:
        """Raise :class:`PolicyError` when the host is not allowlisted."""
        if self.allowed(tenant, host):
            return
        raise PolicyError(
            "host_not_allowlisted",
            retryable=False,
            hint=f"{host!r} is not on this tenant's outbound allowlist",
            context={"host": host, "tenant": tenant},
        )
