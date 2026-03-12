# Embedded MCP Authorization

> **Status**: implementation guide
> **Date**: 2026-03-12

## What it is

Vibrant now protects its HTTP MCP endpoint with a single shared bearer token.

There is no embedded OAuth server, no token exchange flow, and no server-side
scope model. The server accepts requests only when the caller presents the
correct `Authorization: Bearer <token>` header, where the expected token value
is read from an environment variable.

This keeps the transport simple and leaves per-tool permission control to the
Codex side.

## File layout

- `vibrant/mcp/auth.py` — bearer-token parsing and validation helpers
- `vibrant/mcp/config.py` — `MCPServerSettings` and Codex-facing HTTP config
- `vibrant/orchestrator/mcp/fastmcp.py` — FastMCP registration plus HTTP auth wrapper
- `scripts/mcp_dev_server.py` — local MCP launcher for stdio and HTTP

## Core behavior

For HTTP transport:

1. the server reads the expected token from `MCPServerSettings.bearer_token_env_var`
2. each incoming request must include `Authorization: Bearer <token>`
3. if the header is missing or wrong, the server returns `401`
4. if the environment variable is missing, the server returns `500`
5. once the request is authenticated, Vibrant exposes the full MCP surface

For stdio transport:

- no HTTP Authorization header is involved
- the full MCP surface is exposed directly to the local process

## Configuration model

```python
from vibrant.mcp import MCPServerSettings

settings = MCPServerSettings(
    url="http://127.0.0.1:9000/mcp",
    bearer_token_env_var="VIBRANT_MCP_BEARER_TOKEN",
)
```

This one object is used in two places:

- server-side request validation
- Codex-side MCP HTTP configuration via `settings.codex_http_config()`

## Codex configuration

`MCPServerSettings.codex_http_config()` returns the shape Codex expects:

```python
{
    "url": "http://127.0.0.1:9000/mcp",
    "bearer_token_env_var": "VIBRANT_MCP_BEARER_TOKEN",
}
```

That means the same environment variable name can be passed to the MCP service
when it starts and also referenced from the Codex MCP client configuration.

## Server usage

```python
from vibrant.mcp import MCPServerSettings
from vibrant.orchestrator.mcp import (
    OrchestratorMCPServer,
    create_orchestrator_fastmcp_app,
)

settings = MCPServerSettings(
    url="http://127.0.0.1:9000/mcp",
    bearer_token_env_var="VIBRANT_MCP_BEARER_TOKEN",
)

app = create_orchestrator_fastmcp_app(
    registry,
    settings=settings,
    mcp_path="/mcp",
)
```

The expected token value must be present in the process environment, for
example:

```bash
export VIBRANT_MCP_BEARER_TOKEN=development-secret
```

## Permission model

Vibrant no longer filters MCP tools or resources by internal OAuth scopes.

Current behavior is:

- Vibrant authenticates the HTTP request with a bearer token
- Vibrant exposes the full orchestrator MCP surface after authentication
- Codex decides which MCP tools a given agent is allowed to use

That is the intended ownership boundary for permissions.
