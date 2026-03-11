"""Configuration models for Vibrant's embedded auth server."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .authz import MCP_ACCESS_SCOPE, default_role_scopes


class OAuthServerSettings(BaseModel):
    """Configuration for the embedded OAuth authorization server."""

    model_config = ConfigDict(extra="forbid")

    issuer_url: str = "http://localhost:9000"
    authorization_endpoint: str = "/authorize"
    token_endpoint: str = "/token"
    metadata_endpoint: str = "/.well-known/oauth-authorization-server"
    jwks_endpoint: str = "/.well-known/jwks.json"
    default_audience: str = "vibrant-mcp"
    access_token_ttl_seconds: int = 900
    authorization_code_ttl_seconds: int = 300
    require_pkce: bool = True
    pkce_methods: tuple[str, ...] = ("S256",)
    baseline_scopes: tuple[str, ...] = (MCP_ACCESS_SCOPE,)
    role_scopes: dict[str, tuple[str, ...]] = Field(default_factory=default_role_scopes)


__all__ = ["OAuthServerSettings"]
