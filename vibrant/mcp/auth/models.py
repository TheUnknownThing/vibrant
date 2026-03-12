"""Pydantic models for the OAuth authorization server domain."""

from __future__ import annotations

from datetime import datetime, timezone
from secrets import compare_digest
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc)


def normalize_string_list(value: Any) -> tuple[str, ...]:
    """Normalize a string or iterable of strings into a unique ordered tuple."""
    if value is None:
        return ()
    if isinstance(value, str):
        parts = value.split()
    else:
        parts = [str(item).strip() for item in value]
    normalized: list[str] = []
    for item in parts:
        if item and item not in normalized:
            normalized.append(item)
    return tuple(normalized)


def _scope_string(value: tuple[str, ...]) -> str:
    return " ".join(value)


class AuthUser(BaseModel):
    """Stored user record used by the auth server."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    username: str
    roles: tuple[str, ...] = ()
    extra_scopes: tuple[str, ...] = ()
    claims: dict[str, Any] = Field(default_factory=dict)
    active: bool = True

    @field_validator("roles", "extra_scopes", mode="before")
    @classmethod
    def _normalize_list_fields(cls, value: Any) -> tuple[str, ...]:
        return normalize_string_list(value)


class OAuthClient(BaseModel):
    """OAuth client registration.

    The methods on this model intentionally mirror the interfaces Authlib's
    generic authorization server expects from a client object.
    """

    model_config = ConfigDict(extra="forbid")

    client_id: str
    client_secret: str | None = None
    redirect_uris: tuple[str, ...] = ()
    allowed_scopes: tuple[str, ...]
    is_public: bool = True
    require_pkce: bool = True
    display_name: str | None = None
    allowed_grant_types: tuple[str, ...] = ("authorization_code",)
    allowed_response_types: tuple[str, ...] = ("code",)
    token_endpoint_auth_method: str | None = None

    @field_validator(
        "redirect_uris",
        "allowed_scopes",
        "allowed_grant_types",
        "allowed_response_types",
        mode="before",
    )
    @classmethod
    def _normalize_list_fields(cls, value: Any) -> tuple[str, ...]:
        return normalize_string_list(value)

    def get_client_id(self) -> str:
        return self.client_id

    def get_default_redirect_uri(self) -> str:
        return self.redirect_uris[0] if self.redirect_uris else ""

    def get_allowed_scope(self, scope: str | None) -> str | None:
        allowed = set(self.allowed_scopes)
        if not scope:
            return _scope_string(self.allowed_scopes)
        requested = normalize_string_list(scope)
        filtered = tuple(item for item in requested if item in allowed)
        if len(filtered) != len(requested):
            return None
        return _scope_string(filtered)

    def check_redirect_uri(self, redirect_uri: str) -> bool:
        return redirect_uri in self.redirect_uris

    def has_client_secret(self) -> bool:
        return bool(self.client_secret)

    def check_client_secret(self, client_secret: str | None) -> bool:
        if self.client_secret is None:
            return client_secret in {None, ""}
        return compare_digest(self.client_secret, client_secret or "")

    def check_endpoint_auth_method(self, method: str, endpoint: str) -> bool:
        if endpoint != "token":
            return True
        if self.token_endpoint_auth_method:
            return method == self.token_endpoint_auth_method
        if self.is_public or not self.client_secret:
            return method == "none"
        return method in {"client_secret_post", "client_secret_basic"}

    def check_grant_type(self, grant_type: str) -> bool:
        return grant_type in self.allowed_grant_types

    def check_response_type(self, response_type: str) -> bool:
        return response_type in self.allowed_response_types


class AuthorizationRequest(BaseModel):
    """Incoming authorization-code request parameters."""

    model_config = ConfigDict(extra="forbid")

    client_id: str
    redirect_uri: str
    requested_scopes: tuple[str, ...] = ()
    state: str | None = None
    code_challenge: str | None = None
    code_challenge_method: str = "S256"
    audience: str | None = None
    response_type: str = "code"

    @field_validator("requested_scopes", mode="before")
    @classmethod
    def _normalize_scopes(cls, value: Any) -> tuple[str, ...]:
        return normalize_string_list(value)


class AuthorizationDecision(BaseModel):
    """Result of approving an authorization request."""

    model_config = ConfigDict(extra="forbid")

    code: str
    redirect_uri: str
    state: str | None = None
    granted_scopes: tuple[str, ...]
    expires_at: datetime

    @field_validator("granted_scopes", mode="before")
    @classmethod
    def _normalize_scopes(cls, value: Any) -> tuple[str, ...]:
        return normalize_string_list(value)


class AuthorizationCodeGrant(BaseModel):
    """Persisted authorization code grant.

    The helper methods on this model intentionally mirror Authlib's
    ``AuthorizationCodeMixin`` interface.
    """

    model_config = ConfigDict(extra="forbid")

    code: str
    client_id: str
    user_id: str
    redirect_uri: str
    granted_scopes: tuple[str, ...]
    audience: str
    code_challenge: str | None = None
    code_challenge_method: str = "S256"
    state: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime

    @field_validator("granted_scopes", mode="before")
    @classmethod
    def _normalize_scopes(cls, value: Any) -> tuple[str, ...]:
        return normalize_string_list(value)

    def get_redirect_uri(self) -> str:
        return self.redirect_uri

    def get_scope(self) -> str:
        return _scope_string(self.granted_scopes)

    def is_expired(self, now: datetime | None = None) -> bool:
        """Return whether the grant is expired."""
        current_time = now or utc_now()
        return current_time >= self.expires_at


class TokenExchangeRequest(BaseModel):
    """Token endpoint request for the authorization-code grant."""

    model_config = ConfigDict(extra="forbid")

    code: str
    client_id: str
    redirect_uri: str
    code_verifier: str | None = None
    client_secret: str | None = None
    client_auth_method: str | None = None
    grant_type: str = "authorization_code"


class AccessTokenBundle(BaseModel):
    """Token endpoint response payload."""

    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    scope: str
    refresh_token: str | None = None


__all__ = [
    "AccessTokenBundle",
    "AuthorizationCodeGrant",
    "AuthorizationDecision",
    "AuthorizationRequest",
    "AuthUser",
    "OAuthClient",
    "TokenExchangeRequest",
    "normalize_string_list",
    "utc_now",
]
