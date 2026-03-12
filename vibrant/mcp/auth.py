"""Bearer-token helpers for Vibrant's MCP HTTP surface."""

from __future__ import annotations

import os
from collections.abc import Mapping
from secrets import compare_digest


class MCPAuthorizationError(PermissionError):
    """Raised when an MCP request does not present the configured bearer token."""


def extract_bearer_token(authorization_header: str | None) -> str | None:
    """Extract the bearer token from an Authorization header."""

    if authorization_header is None:
        return None

    scheme, separator, token = authorization_header.strip().partition(" ")
    if separator != " " or scheme.lower() != "bearer":
        return None

    normalized = token.strip()
    if not normalized or any(character.isspace() for character in normalized):
        return None
    return normalized


def read_bearer_token(
    *,
    bearer_token_env_var: str,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Read the configured bearer token from the environment."""

    environment = os.environ if environ is None else environ
    token = environment.get(bearer_token_env_var)
    if not token:
        raise MCPAuthorizationError(
            f"Missing MCP bearer token in environment variable {bearer_token_env_var!r}"
        )
    return token


def has_bearer_token(
    authorization_header: str | None,
    *,
    bearer_token_env_var: str,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return whether the request header matches the configured bearer token."""

    presented_token = extract_bearer_token(authorization_header)
    if presented_token is None:
        return False
    expected_token = read_bearer_token(
        bearer_token_env_var=bearer_token_env_var,
        environ=environ,
    )
    return compare_digest(presented_token, expected_token)


def require_bearer_token(
    authorization_header: str | None,
    *,
    bearer_token_env_var: str,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Require a valid bearer token and return the presented token."""

    presented_token = extract_bearer_token(authorization_header)
    if presented_token is None:
        raise MCPAuthorizationError("Missing Authorization: Bearer <token> header")
    if not has_bearer_token(
        authorization_header,
        bearer_token_env_var=bearer_token_env_var,
        environ=environ,
    ):
        raise MCPAuthorizationError("Invalid MCP bearer token")
    return presented_token


__all__ = [
    "MCPAuthorizationError",
    "extract_bearer_token",
    "has_bearer_token",
    "read_bearer_token",
    "require_bearer_token",
]
