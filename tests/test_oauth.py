"""Tests for JWT validation and Principal extraction."""

from __future__ import annotations

import pytest

from atlas_mcp.auth.oauth import Principal, TokenValidator, get_validator
from atlas_mcp.config import ServerSettings
from atlas_mcp.errors.framework import AuthError


def test_dev_token_returns_principal() -> None:
    settings = ServerSettings(auth_dev_token="dev-secret")
    principal = TokenValidator(settings).validate("dev-secret")
    assert principal.subject == "dev:local"
    assert principal.delegator == "user:dev"
    assert principal.tenant == "acme"
    assert principal.has_scope("tool:postgres:read")


def test_dev_token_rejects_wrong_secret() -> None:
    settings = ServerSettings(auth_dev_token="dev-secret")
    with pytest.raises(AuthError) as exc_info:
        TokenValidator(settings).validate("wrong-secret")
    assert exc_info.value.code == "invalid_token"


def test_principal_has_scope_admin_wildcard() -> None:
    principal = Principal(
        subject="agent:test",
        delegator=None,
        tenant="acme",
        scopes=frozenset({"tool:*:admin"}),
        token_id="jti-1",
        issued_at=1,
        expires_at=2,
    )
    assert principal.has_scope("tool:anything:read")


def test_get_validator_is_cached() -> None:
    from atlas_mcp.auth import oauth

    oauth._validator = None
    oauth._validator_key = None
    settings = ServerSettings(auth_dev_token="dev-secret")
    assert get_validator(settings) is get_validator(settings)
