from __future__ import annotations

import pytest

from vibrant.mcp import (
    MCPAuthorizationError,
    MCPServerSettings,
    extract_bearer_token,
    read_bearer_token,
    require_bearer_token,
)


def test_extract_bearer_token_parses_standard_header() -> None:
    assert extract_bearer_token("Bearer secret-token") == "secret-token"
    assert extract_bearer_token("bearer secret-token") == "secret-token"
    assert extract_bearer_token("Basic secret-token") is None
    assert extract_bearer_token("Bearer") is None
    assert extract_bearer_token(None) is None


def test_require_bearer_token_accepts_matching_env_var() -> None:
    token = require_bearer_token(
        "Bearer secret-token",
        bearer_token_env_var="VIBRANT_MCP_TOKEN",
        environ={"VIBRANT_MCP_TOKEN": "secret-token"},
    )

    assert token == "secret-token"


def test_require_bearer_token_rejects_missing_or_invalid_tokens() -> None:
    with pytest.raises(MCPAuthorizationError, match="Authorization: Bearer"):
        require_bearer_token(
            None,
            bearer_token_env_var="VIBRANT_MCP_TOKEN",
            environ={"VIBRANT_MCP_TOKEN": "secret-token"},
        )

    with pytest.raises(MCPAuthorizationError, match="Invalid MCP bearer token"):
        require_bearer_token(
            "Bearer wrong-token",
            bearer_token_env_var="VIBRANT_MCP_TOKEN",
            environ={"VIBRANT_MCP_TOKEN": "secret-token"},
        )

    with pytest.raises(MCPAuthorizationError, match="environment variable"):
        read_bearer_token(
            bearer_token_env_var="VIBRANT_MCP_TOKEN",
            environ={},
        )


def test_mcp_server_settings_emit_codex_http_config() -> None:
    settings = MCPServerSettings(
        url="https://mcp.example.com",
        bearer_token_env_var="VIBRANT_MCP_TOKEN",
    )

    assert settings.codex_http_config() == {
        "url": "https://mcp.example.com",
        "bearer_token_env_var": "VIBRANT_MCP_TOKEN",
    }
    assert settings.authorization_header_value(
        environ={"VIBRANT_MCP_TOKEN": "secret-token"}
    ) == "Bearer secret-token"
    assert settings.require_authorization(
        "Bearer secret-token",
        environ={"VIBRANT_MCP_TOKEN": "secret-token"},
    ) == "secret-token"
