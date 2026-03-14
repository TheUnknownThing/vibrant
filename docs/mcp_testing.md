# Testing the MCP Server

> **Status**: developer guide
> **Date**: 2026-03-12

This guide explains how to run Vibrant's MCP server locally and verify the
current bearer-token transport behavior.

## Prerequisites

- Python dependencies installed with `uv`
- optional MCP dependencies installed:

```bash
uv sync --extra mcp --dev
```

## Start the server over stdio

The repo includes a small dev launcher at `scripts/mcp_dev_server.py`.

For a clean local test project, use a temporary project root instead of the repository root. This avoids loading old `.vibrant/agent-runs/*.json` records that may not match the current schema.

This is the simplest way to explore the MCP surface locally because no HTTP
auth is involved.

```bash
uv run python scripts/mcp_dev_server.py \
  --project-root /tmp/vibrant-mcp-demo \
  --transport stdio
```

## Start the server over HTTP

HTTP transport now requires a bearer token stored in an environment variable.

```bash
export VIBRANT_MCP_BEARER_TOKEN=development-secret

uv run python scripts/mcp_dev_server.py \
  --project-root /tmp/vibrant-mcp-demo \
  --host 127.0.0.1 \
  --port 9000
```

The MCP endpoint will be:

```text
http://127.0.0.1:9000/mcp
```

If you want a different environment variable name, pass it explicitly:

```bash
export CUSTOM_MCP_TOKEN=development-secret

uv run python scripts/mcp_dev_server.py \
  --project-root /tmp/vibrant-mcp-demo \
  --host 127.0.0.1 \
  --port 9000 \
  --bearer-token-env-var CUSTOM_MCP_TOKEN
```

## Quick HTTP verification

Without the header, the server should reject the request:

```bash
curl -i http://127.0.0.1:9000/mcp
```

With the correct header, the request should reach the MCP app:

```bash
curl -i \
  -H 'Authorization: Bearer development-secret' \
  http://127.0.0.1:9000/mcp
```

The MCP app may still return a protocol-level error for an incomplete request,
but it should no longer fail with `401`.

## What to test manually

For stdio or authenticated HTTP sessions, verify:

- resources such as `vibrant://consensus/current`, `vibrant://roadmap/current`, and `vibrant://workflow/status`
- safe reads such as `consensus_get`, `roadmap_get`, `task_get`, and `instance_list`
- safe mutations in a disposable project such as `workflow_pause`, `workflow_resume`, `vibrant.update_consensus`, and `vibrant.update_roadmap`

## Automated tests

Relevant tests include:

- `tests/test_mcp_bearer_auth.py`
- `tests/test_orchestrator_mcp.py`
- `tests/test_orchestrator_fastmcp.py`

Example targeted runs:

```bash
uv run pytest -q tests/test_mcp_bearer_auth.py
uv run pytest -q tests/test_orchestrator_mcp.py
uv run pytest -q tests/test_orchestrator_fastmcp.py
```

## Current permission model

The server does not perform per-tool authorization after transport auth.

Current behavior is:

- HTTP requests must present the configured bearer token
- stdio transport is trusted locally
- once connected, the full MCP surface is available
- Codex is responsible for constraining which tools a particular agent may call

## Troubleshooting

### `401 unauthorized`

The request did not include the expected bearer token.

Check:

- the header format is exactly `Authorization: Bearer <token>`
- the token value matches the server environment variable
- the server was started with the expected `--bearer-token-env-var`

### `500 server_error`

The configured environment variable is missing in the server process.

Set it before starting the HTTP server, for example:

```bash
export VIBRANT_MCP_BEARER_TOKEN=development-secret
```

### `address already in use`

Another process is already listening on the chosen port. Pick a different port:

```bash
uv run python scripts/mcp_dev_server.py \
  --project-root /tmp/vibrant-mcp-demo \
  --host 127.0.0.1 \
  --port 9001
```

## Related docs

- `docs/embedded_auth.md`
- `vibrant/orchestrator/mcp/MCP.md`
- `scripts/mcp_dev_server.py`
