# Testing the MCP Server

> **Status**: developer guide
> **Date**: 2026-03-15

This guide covers the current orchestrator-owned MCP surface: a loopback
FastMCP HTTP host with per-run access enforced by registered bindings.

## Prerequisites

- Python dependencies installed with `uv`
- Optional MCP dependencies installed:
- If your shell sets `HTTP_PROXY` or `HTTPS_PROXY`, ensure `NO_PROXY` includes
  `127.0.0.1,localhost` so loopback MCP traffic stays local

```bash
uv sync --extra mcp --dev
```

## Current transport model

- The dev launcher is `scripts/mcp_dev_server.py`
- HTTP is the supported transport for this launcher
- The server uses a binding header, not bearer-token auth
- By default, it runs in stateless Streamable HTTP mode

The required request header is:

```text
X-Vibrant-Binding: <binding-id>
```

The binding id is printed when the server starts.

## Start the server (local machine)

Use a disposable project root for clean test data:

```bash
uv run python scripts/mcp_dev_server.py \
  --project-root /tmp/vibrant-mcp-demo \
  --role gatekeeper \
  --host 127.0.0.1 \
  --port 9000
```

Expected startup output includes:

- Endpoint URL (for example `http://127.0.0.1:9000/mcp`)
- Required `X-Vibrant-Binding` header value
- Stateless HTTP mode status

## Start the server (LAN access)

If a client runs on another machine in the same LAN, bind to your LAN IP:

```bash
uv run python scripts/mcp_dev_server.py \
  --project-root /tmp/vibrant-mcp-demo \
  --role gatekeeper \
  --host 10.80.73.51 \
  --port 9000
```

Use that same IP in the client URL.

## Connect with MCP Inspector

Start Inspector:

```bash
npx @modelcontextprotocol/inspector
```

In Inspector, use:

- Transport: Streamable HTTP
- URL: `http://<server-host>:<server-port>/mcp`
- Header key: `X-Vibrant-Binding`
- Header value: the binding id printed by server startup

Notes:

- For host `0.0.0.0`, clients should still connect using a concrete IP or DNS name
- Binding ids are generated per server start; refresh the header value after restart

## Optional server modes and flags

- Stateful mode:

```bash
uv run python scripts/mcp_dev_server.py \
  --project-root /tmp/vibrant-mcp-demo \
  --host 127.0.0.1 \
  --port 9000 \
  --stateful-http
```

In stateful mode, the client must preserve and resend `Mcp-Session-Id`.

- Worker-scoped binding:

```bash
uv run python scripts/mcp_dev_server.py \
  --project-root /tmp/vibrant-mcp-demo \
  --role worker \
  --worker-agent-id dev-worker \
  --worker-agent-type code
```

- Explicit CORS origins (repeatable):

```bash
uv run python scripts/mcp_dev_server.py \
  --project-root /tmp/vibrant-mcp-demo \
  --host 10.80.73.51 \
  --cors-allow-origin http://10.80.73.54:6274
```

If no CORS origin is provided, the dev server allows all origins.

## Quick HTTP verification

Check browser preflight behavior:

```bash
curl -i -X OPTIONS 'http://127.0.0.1:9000/mcp' \
  -H 'Origin: http://127.0.0.1:6274' \
  -H 'Access-Control-Request-Method: POST' \
  -H 'Access-Control-Request-Headers: content-type,x-vibrant-binding'
```

Expected result: `200 OK` with `access-control-allow-*` headers.

Protocol-level request example with required headers:

```bash
curl -i 'http://127.0.0.1:9000/mcp' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -H 'X-Vibrant-Binding: <binding-id>' \
  --data '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

## What to test manually

With a gatekeeper binding, verify that expected tools and resources are visible
and callable.

With a worker binding, verify that the server-side surface is filtered.

Example checks:

- list tools and confirm only expected entries are exposed
- list resources and read known resources such as `vibrant://consensus`
- call `vibrant.add_task` and read back via `vibrant://tasks/<task-id>`

## Automated tests

Relevant tests:

- `tests/test_orchestrator_mcp_transport.py`
- `tests/test_provider_invocation_compiler.py`
- `tests/test_orchestrator_mcp_surface.py`

Example targeted runs:

```bash
uv run pytest -q tests/test_orchestrator_mcp_transport.py
uv run pytest -q tests/test_provider_invocation_compiler.py
uv run pytest -q tests/test_orchestrator_mcp_surface.py
```

These cover the loopback FastMCP host, binding-based filtering, and provider
invocation plans that inject the MCP endpoint plus `X-Vibrant-Binding`.

## Troubleshooting

### 421 Misdirected Request with Invalid Host header

The request Host is not allowed by transport security.

Fix:

- start the server with the exact LAN IP or hostname clients will use
- connect clients to that same host value

### 405 Method Not Allowed on OPTIONS

A preflight request reached an app path without CORS handling.

Fix:

- use the current `scripts/mcp_dev_server.py` (it installs CORS middleware)
- restart any old server process still running an older script version

### -32600 Bad Request: Missing session ID

The server is in stateful mode, but the client is not sending `Mcp-Session-Id`.

Fix:

- prefer default stateless mode (no `--stateful-http`)
- or update the client to preserve and resend `Mcp-Session-Id`

### address already in use

Choose a different port:

```bash
uv run python scripts/mcp_dev_server.py \
  --project-root /tmp/vibrant-mcp-demo \
  --host 127.0.0.1 \
  --port 9001
```

## Related docs

- `docs/mcp_testing.md`
- `scripts/mcp_dev_server.py`
