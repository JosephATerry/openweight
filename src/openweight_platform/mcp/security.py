"""MCP adapters for the existing application authentication boundary."""

from __future__ import annotations

from collections.abc import Callable

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings

from openweight_platform.api.config import ServiceSettings
from openweight_platform.api.errors import (
    AuthenticationRequiredError,
    PermissionDeniedError,
)
from openweight_platform.api.security import AuthorizationBoundary, Permission


class McpTokenVerifier:
    """Adapt the D14 JWT verifier to the official SDK verifier protocol."""

    def __init__(self, authorization: AuthorizationBoundary) -> None:
        self._authorization = authorization

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            principal = self._authorization.authenticate_token(token)
        except (AuthenticationRequiredError, PermissionDeniedError):
            return None
        return AccessToken(
            token=token,
            client_id=principal.subject,
            subject=principal.subject,
            scopes=sorted(principal.permissions),
        )


AccessTokenGetter = Callable[[], AccessToken | None]


class McpAuthorization:
    """Enforce the existing bounded permissions inside each MCP handler."""

    def __init__(
        self,
        *,
        enabled: bool,
        access_token_getter: AccessTokenGetter = get_access_token,
    ) -> None:
        self._enabled = enabled
        self._access_token_getter = access_token_getter

    def require(self, permission: Permission) -> str:
        if not self._enabled:
            return "local-auth-disabled"
        access_token = self._access_token_getter()
        if access_token is None:
            raise AuthenticationRequiredError
        if permission not in access_token.scopes:
            raise PermissionDeniedError
        if not access_token.subject:
            raise AuthenticationRequiredError
        return access_token.subject


def sdk_auth_settings(settings: ServiceSettings) -> AuthSettings | None:
    """Build resource-server metadata only for explicitly enabled auth."""

    if not settings.auth_enabled:
        return None
    if settings.auth_issuer is None or settings.mcp_resource_server_url is None:
        raise ValueError("authenticated MCP configuration is incomplete")
    return AuthSettings(
        issuer_url=settings.auth_issuer,
        resource_server_url=settings.mcp_resource_server_url,
        required_scopes=[],
    )


__all__ = [
    "McpAuthorization",
    "McpTokenVerifier",
    "sdk_auth_settings",
]
