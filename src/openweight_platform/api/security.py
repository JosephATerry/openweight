"""Standards-based bearer authentication and bounded authorization policy."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import jwt
from jwt import PyJWKClient

from openweight_platform.api.config import ServiceSettings
from openweight_platform.api.errors import (
    AuthenticationRequiredError,
    PermissionDeniedError,
)


Permission = Literal[
    "agent.query",
    "actions.propose",
    "approvals.resume",
    "metrics.read",
]

ROLE_PERMISSIONS: Mapping[str, frozenset[Permission]] = {
    "OpenWeight.Reader": frozenset({"agent.query"}),
    "OpenWeight.Approver": frozenset({"agent.query", "approvals.resume"}),
    "OpenWeight.Operator": frozenset(
        {"agent.query", "actions.propose", "approvals.resume", "metrics.read"}
    ),
}
KNOWN_SCOPES = frozenset(
    {
        "agent.query",
        "actions.propose",
        "approvals.resume",
        "metrics.read",
    }
)


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    subject: str
    permissions: frozenset[Permission]


class TokenVerifier(Protocol):
    def verify(self, token: str) -> AuthenticatedPrincipal: ...


KeyResolver = Callable[[str], Any]


class JwtTokenVerifier:
    """Validate RS256 JWTs against a configured issuer/audience and JWKS."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        key_resolver: KeyResolver | None = None,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._jwk_client = PyJWKClient(jwks_url, cache_keys=True)
        self._key_resolver = key_resolver or self._resolve_jwks_key

    def _resolve_jwks_key(self, token: str) -> Any:
        return self._jwk_client.get_signing_key_from_jwt(token).key

    def verify(self, token: str) -> AuthenticatedPrincipal:
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256":
                raise AuthenticationRequiredError
            key = self._key_resolver(token)
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except AuthenticationRequiredError:
            raise
        except Exception as error:
            raise AuthenticationRequiredError from error

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise AuthenticationRequiredError
        permissions: set[Permission] = set()
        roles = claims.get("roles", [])
        if isinstance(roles, str):
            roles = [roles]
        if isinstance(roles, list):
            for role in roles:
                if isinstance(role, str):
                    permissions.update(ROLE_PERMISSIONS.get(role, ()))
        scope_claim = claims.get("scp", "")
        if isinstance(scope_claim, str):
            permissions.update(
                scope  # type: ignore[arg-type]
                for scope in scope_claim.split()
                if scope in KNOWN_SCOPES
            )
        return AuthenticatedPrincipal(
            subject=subject,
            permissions=frozenset(permissions),
        )


class AuthorizationBoundary:
    """Enforce authentication only when configured, with explicit permissions."""

    def __init__(
        self,
        settings: ServiceSettings,
        verifier: TokenVerifier | None = None,
    ) -> None:
        self._enabled = settings.auth_enabled
        if self._enabled and verifier is None and all(
            (
                settings.auth_issuer,
                settings.auth_audience,
                settings.auth_jwks_url,
            )
        ):
            verifier = JwtTokenVerifier(
                issuer=settings.auth_issuer,  # type: ignore[arg-type]
                audience=settings.auth_audience,  # type: ignore[arg-type]
                jwks_url=settings.auth_jwks_url,  # type: ignore[arg-type]
            )
        self._verifier = verifier

    def require(
        self,
        authorization: str | None,
        permission: Permission,
    ) -> AuthenticatedPrincipal:
        principal = self.authenticate(authorization)
        if permission not in principal.permissions:
            raise PermissionDeniedError
        return principal

    def authenticate(
        self,
        authorization: str | None,
    ) -> AuthenticatedPrincipal:
        """Authenticate one bearer header without granting an operation."""

        if not self._enabled:
            return AuthenticatedPrincipal(
                subject="local-auth-disabled",
                permissions=KNOWN_SCOPES,  # type: ignore[arg-type]
            )
        if authorization is None or not authorization.startswith("Bearer "):
            raise AuthenticationRequiredError
        token = authorization[7:].strip()
        return self.authenticate_token(token)

    def authenticate_token(self, token: str) -> AuthenticatedPrincipal:
        """Authenticate a raw token supplied by a protocol bearer adapter."""

        if not self._enabled:
            return AuthenticatedPrincipal(
                subject="local-auth-disabled",
                permissions=KNOWN_SCOPES,  # type: ignore[arg-type]
            )
        if not token or self._verifier is None:
            raise AuthenticationRequiredError
        return self._verifier.verify(token)


__all__ = [
    "AuthenticatedPrincipal",
    "AuthorizationBoundary",
    "JwtTokenVerifier",
    "KNOWN_SCOPES",
    "Permission",
    "ROLE_PERMISSIONS",
    "TokenVerifier",
]
