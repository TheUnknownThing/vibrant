# Testing the MCP Server

> **Status**: developer guide  
> **Date**: 2026-03-12

This guide explains how to run Vibrant's MCP server locally, connect the official MCP Inspector, and understand the current state of permission testing.

## Prerequisites

- Python dependencies installed with `uv`
- Node.js available for the official MCP Inspector
- optional MCP dependencies installed:

```bash
uv sync --extra mcp --dev
```

## Quick start

The repo includes a small dev launcher at `scripts/mcp_dev_server.py`.

For a clean local test project, use a temporary project root instead of the repository root. This avoids loading old `.vibrant/agent-runs/*.json` records that may not match the current schema.

### Start the server over stdio

This is the easiest way to test with the official MCP Inspector.

```bash
uv run python scripts/mcp_dev_server.py \
  --project-root /tmp/vibrant-mcp-demo \
  --transport stdio
```

### Start the server over HTTP

```bash
uv run python scripts/mcp_dev_server.py \
  --project-root /tmp/vibrant-mcp-demo \
  --host 127.0.0.1 \
  --port 8000
```

The MCP endpoint will be:

```text
http://127.0.0.1:8000/mcp
```

If port `8000` is already in use, pick another port such as `8001`.

## Using the official MCP Inspector

The official Inspector is the easiest human-facing tool for exploring resources and tools.

### Inspector with stdio

This is the most convenient path because Inspector launches the server directly.

```bash
npx -y @modelcontextprotocol/inspector \
  uv run python scripts/mcp_dev_server.py \
  --project-root /tmp/vibrant-mcp-demo \
  --transport stdio
```

### Inspector with HTTP

First start the server in a separate terminal:

```bash
uv run python scripts/mcp_dev_server.py \
  --project-root /tmp/vibrant-mcp-demo \
  --host 127.0.0.1 \
  --port 8000
```

Then start Inspector:

```bash
npx -y @modelcontextprotocol/inspector
```

In Inspector:

- choose `Streamable HTTP` or `HTTP`
- set the server URL to `http://127.0.0.1:8000/mcp`
- connect

## What to test in Inspector

After connecting, use Inspector to verify that the MCP surface is wired correctly.

### Read-only checks

- open **Resources** and inspect:
  - `vibrant://consensus/current`
  - `vibrant://roadmap/current`
  - `vibrant://workflow/status`
- open **Tools** and call:
  - `consensus_get`
  - `roadmap_get`

### Safe write checks

You can also try a few state-changing tools in a disposable test project:

- `workflow_pause`
- `workflow_resume`
- `vibrant.update_consensus`
- `vibrant.update_roadmap`

After calling a write tool, re-read the related resource to confirm the change.

## Current permissions caveat

The current dev launcher in `scripts/mcp_dev_server.py` starts the MCP server **without** an auth provider.

That means:

- it is useful for transport and surface testing
- it is useful for basic tool/resource exploration
- it does **not** exercise bearer-token authorization
- it does **not** validate role-based permission boundaries

When auth is omitted, the server falls back to a trusted local principal with all required scopes.

## How to test permissions today

For now, permission behavior is best validated through the existing automated tests rather than Inspector.

Relevant tests include:

- `tests/test_orchestrator_mcp.py`
- `tests/test_orchestrator_fastmcp.py`
- `tests/test_mcp_auth_service.py`

Example targeted test runs:

```bash
uv run pytest -q tests/test_orchestrator_mcp.py
uv run pytest -q tests/test_mcp_auth_service.py
uv run pytest -q tests/test_orchestrator_fastmcp.py -k 'embedded_oauth_provider_verifies_service_tokens or app_exposes_auth_and_resource_routes'
```

These cover:

- scope enforcement in the MCP registry
- token minting and verification
- HTTP exposure of the auth-enabled FastMCP app

## When you want auth-enabled manual testing

For real manual permission testing in Inspector, the server needs to be started with `EmbeddedOAuthProvider` and an `AuthorizationServerService`.

That setup should provide:

- an HTTP MCP endpoint protected by bearer tokens
- token minting for test identities such as `agent` and `gatekeeper`
- a simple way to launch Inspector with the correct URL and test token

At the time of writing, the repo does not yet include a dedicated auth-enabled dev launcher.

## Troubleshooting

### `address already in use`

Another process is already listening on the chosen port.

Use a different port:

```bash
uv run python scripts/mcp_dev_server.py \
  --project-root /tmp/vibrant-mcp-demo \
  --host 127.0.0.1 \
  --port 8001
```

### `command not found: --port`

Your shell treated the next line as a separate command.

Either keep the command on one line:

```bash
uv run python scripts/mcp_dev_server.py --project-root /tmp/vibrant-mcp-demo --host 127.0.0.1 --port 8000
```

or use line continuations with trailing `\` characters.

### Inspector asks for manual connection details

Use the stdio form so Inspector launches the server command directly:

```bash
npx -y @modelcontextprotocol/inspector \
  uv run python scripts/mcp_dev_server.py \
  --project-root /tmp/vibrant-mcp-demo \
  --transport stdio
```

## Related docs

- `vibrant/orchestrator/mcp/MCP.md`
- `docs/embedded_auth.md`
- `scripts/mcp_dev_server.py`
