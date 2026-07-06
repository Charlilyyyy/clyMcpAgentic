"""OAuth 2.1 resource-server authentication — JWT validation against JWKS."""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
import jwt
from jwt import PyJWKClient

from atlas_mcp.config import ServerSettings
from atlas_mcp.errors.framework import AuthError


@dataclass(frozen=True, slots=True)
class Principal:
    """Caller identity produced by successful token validation."""

    subject: str
    delegator: str | None
    tenant: str
    scopes: frozenset[str]
    token_id: str
    issued_at: int
    expires_at: int

    def has_scope(self, required: str) -> bool:
        return required in self.scopes or "tool:*:admin" in self.scopes


class TokenValidator:
    """Validates bearer tokens against the authorization server JWKS."""

    def __init__(self, settings: ServerSettings):
        self.settings = settings
        self._jwks = PyJWKClient(settings.auth_jwks_url, cache_keys=True, max_cached_keys=16)

    def validate(self, bearer: str) -> Principal:
        if self.settings.auth_dev_token and bearer == self.settings.auth_dev_token:
            return _dev_principal()

        try:
            signing_key = self._jwks.get_signing_key_from_jwt(bearer)
            claims = jwt.decode(
                bearer,
                signing_key.key,
                algorithms=["RS256", "ES256"],
                audience=self.settings.auth_audience,
                issuer=self.settings.auth_issuer,
                options={"require": ["exp", "iat", "sub", "jti", "aud", "iss"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("token_expired", retryable=True, hint="refresh your token") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthError("invalid_token", retryable=False, hint=str(exc)) from exc

        scopes = claims.get("scope", "")
        scope_set = frozenset(scopes.split() if isinstance(scopes, str) else scopes)
        act = claims.get("act") or {}

        return Principal(
            subject=claims["sub"],
            delegator=act.get("sub"),
            tenant=claims.get("tenant", "default"),
            scopes=scope_set,
            token_id=claims["jti"],
            issued_at=claims["iat"],
            expires_at=claims["exp"],
        )


def _dev_principal() -> Principal:
    """Fixed principal for local development tokens."""
    issued = now()
    return Principal(
        subject="dev:local",
        delegator="user:dev",
        tenant="acme",
        scopes=frozenset({"tool:*:admin"}),
        token_id="dev",
        issued_at=issued,
        expires_at=issued + 3600,
    )


_validator: TokenValidator | None = None
_validator_key: tuple[str, str, str, str | None] | None = None


def get_validator(settings: ServerSettings) -> TokenValidator:
    global _validator, _validator_key
    key = (
        settings.auth_jwks_url,
        settings.auth_audience,
        settings.auth_issuer,
        settings.auth_dev_token,
    )
    if _validator is None or _validator_key != key:
        _validator = TokenValidator(settings)
        _validator_key = key
    return _validator


async def discover_authorization_server(issuer_url: str) -> dict:
    """Fetch RFC 8414 authorization server metadata."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(f"{issuer_url.rstrip('/')}/.well-known/oauth-authorization-server")
        response.raise_for_status()
        return response.json()


def now() -> int:
    return int(time.time())
