"""Embedded OAuth helpers for Vibrant's MCP surface."""

from .fastapi_app import create_auth_app
from .models import (
    AccessTokenBundle,
    AuthorizationCodeGrant,
    AuthorizationDecision,
    AuthorizationRequest,
    AuthUser,
    OAuthClient,
    TokenExchangeRequest,
)
from .service import (
    AuthorizationServerService,
    OAuthError,
    ScopeResolutionError,
    build_s256_code_challenge,
    expand_role_scopes,
    normalize_scopes,
    resolve_scopes,
    scope_string,
)
from .store import AuthStore, InMemoryAuthStore
from .tokens import HMACTokenSigner, TokenSigner

__all__ = [
    "AccessTokenBundle",
    "AuthStore",
    "AuthUser",
    "AuthorizationCodeGrant",
    "AuthorizationDecision",
    "AuthorizationRequest",
    "AuthorizationServerService",
    "HMACTokenSigner",
    "InMemoryAuthStore",
    "OAuthClient",
    "OAuthError",
    "ScopeResolutionError",
    "TokenExchangeRequest",
    "TokenSigner",
    "build_s256_code_challenge",
    "create_auth_app",
    "expand_role_scopes",
    "normalize_scopes",
    "resolve_scopes",
    "scope_string",
]
