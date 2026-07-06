"""Tests for per-tenant outbound HTTP allowlist enforcement."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from atlas_mcp.errors.framework import PolicyError
from atlas_mcp.governance.http_allowlist import HttpAllowlist
from atlas_mcp.server import _enforce_http_allowlist


class _FetchArgs(BaseModel):
    url: str


def test_exact_host_allowed_for_tenant() -> None:
    allowlist = HttpAllowlist({"acme": ["api.stripe.com"]})
    assert allowlist.allowed("acme", "api.stripe.com")


def test_unknown_host_denied() -> None:
    allowlist = HttpAllowlist({"acme": ["api.stripe.com"]})
    with pytest.raises(PolicyError) as exc_info:
        allowlist.check("acme", "evil.example.com")
    assert exc_info.value.code == "host_not_allowlisted"


def test_wildcard_subdomain_match() -> None:
    allowlist = HttpAllowlist({"acme": ["*.acme.internal"]})
    assert allowlist.allowed("acme", "docs.acme.internal")
    assert not allowlist.allowed("acme", "acme.internal")


def test_global_hosts_apply_to_all_tenants() -> None:
    allowlist = HttpAllowlist(
        {
            "*": ["api.company-status-page.internal"],
            "globex": ["api.twilio.com"],
        }
    )
    assert allowlist.allowed("acme", "api.company-status-page.internal")
    assert allowlist.allowed("globex", "api.twilio.com")
    assert not allowlist.allowed("acme", "api.twilio.com")


def test_missing_file_means_deny_all() -> None:
    allowlist = HttpAllowlist.from_file("config/does-not-exist.yaml")
    with pytest.raises(PolicyError):
        allowlist.check("acme", "api.stripe.com")


def test_loads_project_allowlist_file() -> None:
    allowlist = HttpAllowlist.from_file("config/http_allowlist.yaml")
    assert allowlist.allowed("acme", "api.stripe.com")
    assert allowlist.allowed("acme", "billing.acme.internal")
    assert not allowlist.allowed("acme", "api.twilio.com")


def test_dispatch_pipeline_checks_url_hosts() -> None:
    allowlist = HttpAllowlist({"acme": ["api.stripe.com"]})
    _enforce_http_allowlist(
        allowlist,
        "acme",
        _FetchArgs(url="https://api.stripe.com/v1/charges"),
    )
    with pytest.raises(PolicyError):
        _enforce_http_allowlist(
            allowlist,
            "acme",
            _FetchArgs(url="https://evil.example.com/exfil"),
        )
