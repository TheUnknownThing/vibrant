"""Configuration models for Vibrant's bearer-token MCP surface."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from .auth import read_bearer_token, require_bearer_token

DEFAULT_MCP_SERVER_URL = "http://localhost:9000/mcp"
DEFAULT_BEARER_TOKEN_ENV_VAR = "VIBRANT_MCP_BEARER_TOKEN"


class MCPServerSettings(BaseModel):
    """Configuration for a streamable HTTP MCP server consumed by Codex."""

    model_config = ConfigDict(extra="forbid")

    url: str = DEFAULT_MCP_SERVER_URL
    bearer_token_env_var: str = Field(default=DEFAULT_BEARER_TOKEN_ENV_VAR, min_length=1)

    def codex_http_config(self) -> dict[str, str]:
        """Return the Codex MCP HTTP config using the supported field names."""

        return {
            "url": self.url,
            "bearer_token_env_var": self.bearer_token_env_var,
        }

    def authorization_header_value(self, *, environ: Mapping[str, str] | None = None) -> str:
        """Build the Authorization header value from the configured environment variable."""

        token = read_bearer_token(
            bearer_token_env_var=self.bearer_token_env_var,
            environ=environ,
        )
        return f"Bearer {token}"

    def require_authorization(
        self,
        authorization_header: str | None,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> str:
        """Require a valid bearer token for an incoming MCP request."""

        return require_bearer_token(
            authorization_header,
            bearer_token_env_var=self.bearer_token_env_var,
            environ=environ,
        )


__all__ = [
    "DEFAULT_BEARER_TOKEN_ENV_VAR",
    "DEFAULT_MCP_SERVER_URL",
    "MCPServerSettings",
]
