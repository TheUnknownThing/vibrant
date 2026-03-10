"""Storage protocol and in-memory implementation for the auth server."""

from __future__ import annotations

from typing import Any, Protocol

from .models import AuthorizationCodeGrant, AuthUser, OAuthClient


class AuthStore(Protocol):
    """Minimal storage interface required by the auth server service."""

    def save_user(self, user: AuthUser) -> AuthUser:
        ...

    def get_user(self, user_id: str) -> AuthUser | None:
        ...

    def save_client(self, client: OAuthClient) -> OAuthClient:
        ...

    def get_client(self, client_id: str) -> OAuthClient | None:
        ...

    def save_authorization_code(self, grant: AuthorizationCodeGrant) -> AuthorizationCodeGrant:
        ...

    def get_authorization_code(
        self,
        code: str,
        client_id: str | None = None,
    ) -> AuthorizationCodeGrant | None:
        ...

    def delete_authorization_code(self, code: str) -> None:
        ...

    def save_access_token(self, token: dict[str, Any]) -> None:
        ...


class InMemoryAuthStore:
    """Simple in-memory store suitable for tests and local development."""

    def __init__(self) -> None:
        self._users: dict[str, AuthUser] = {}
        self._clients: dict[str, OAuthClient] = {}
        self._authorization_codes: dict[str, AuthorizationCodeGrant] = {}
        self._access_tokens: list[dict[str, Any]] = []

    def save_user(self, user: AuthUser) -> AuthUser:
        self._users[user.user_id] = user
        return user

    def get_user(self, user_id: str) -> AuthUser | None:
        return self._users.get(user_id)

    def save_client(self, client: OAuthClient) -> OAuthClient:
        self._clients[client.client_id] = client
        return client

    def get_client(self, client_id: str) -> OAuthClient | None:
        return self._clients.get(client_id)

    def save_authorization_code(self, grant: AuthorizationCodeGrant) -> AuthorizationCodeGrant:
        self._authorization_codes[grant.code] = grant
        return grant

    def get_authorization_code(
        self,
        code: str,
        client_id: str | None = None,
    ) -> AuthorizationCodeGrant | None:
        grant = self._authorization_codes.get(code)
        if grant is None:
            return None
        if client_id is not None and grant.client_id != client_id:
            return None
        return grant

    def delete_authorization_code(self, code: str) -> None:
        self._authorization_codes.pop(code, None)

    def save_access_token(self, token: dict[str, Any]) -> None:
        self._access_tokens.append(dict(token))


__all__ = ["AuthStore", "InMemoryAuthStore"]
