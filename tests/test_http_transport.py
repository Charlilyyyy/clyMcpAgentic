"""Tests for HTTP transport endpoints."""

from __future__ import annotations

from starlette.testclient import TestClient

from atlas_mcp.config import ServerSettings
from atlas_mcp.server import AtlasServer
from atlas_mcp.transport.http import build_http_app


def test_healthz_returns_ok() -> None:
    server = AtlasServer(ServerSettings())
    with TestClient(build_http_app(server)) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_returns_ready_after_startup() -> None:
    server = AtlasServer(ServerSettings())
    with TestClient(build_http_app(server)) as client:
        response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_well_known_lists_stub_tool() -> None:
    server = AtlasServer(ServerSettings())
    with TestClient(build_http_app(server)) as client:
        response = client.get("/.well-known/mcp-server")
    assert response.status_code == 200
    payload = response.json()
    assert payload["server"]["name"] == "atlas-mcp"
    assert any(tool["name"] == "server.ping" for tool in payload["tools_summary"])


def test_mcp_rejects_missing_bearer_token() -> None:
    server = AtlasServer(ServerSettings(auth_dev_token="dev-secret"))
    with TestClient(build_http_app(server)) as client:
        response = client.post("/mcp/")
    assert response.status_code == 401
    assert response.json() == {"error": "missing_token"}
    assert "WWW-Authenticate" in response.headers
    assert 'error="missing_token"' in response.headers["WWW-Authenticate"]


def test_mcp_rejects_invalid_bearer_token() -> None:
    server = AtlasServer(ServerSettings(auth_dev_token="dev-secret"))
    with TestClient(build_http_app(server)) as client:
        response = client.post(
            "/mcp/",
            headers={"Authorization": "Bearer wrong-secret"},
        )
    assert response.status_code == 401
    assert response.json() == {"error": "invalid_token"}


def test_mcp_rejects_cross_tenant_header_without_impersonate_scope() -> None:
    server = AtlasServer(ServerSettings(auth_dev_token="dev-secret"))
    with TestClient(build_http_app(server)) as client:
        response = client.post(
            "/mcp/",
            headers={
                "Authorization": "Bearer dev-secret",
                "X-Tenant-Id": "globex",
            },
        )
    assert response.status_code == 403
    assert response.json() == {"error": "tenant_mismatch"}
