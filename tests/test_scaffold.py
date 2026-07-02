"""Smoke tests for package scaffold — ensures install and imports work."""

from __future__ import annotations

from atlas_mcp import __version__
from atlas_mcp.config import ServerSettings, get_settings


def test_version() -> None:
    assert __version__ == "0.1.0"


def test_settings_defaults() -> None:
    settings = ServerSettings()
    assert settings.transport == "http"
    assert settings.http_port == 8080
    assert settings.policy_default_deny is True
    assert settings.policy_file == "config/policy.yaml"


def test_get_settings_cached() -> None:
    get_settings.cache_clear()
    a = get_settings()
    b = get_settings()
    assert a is b
