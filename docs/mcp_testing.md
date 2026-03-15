# Testing The MCP Surface

> Status: developer guide
> Date: 2026-03-15

This guide reflects the current orchestrator MCP model:

- the active transport is the orchestrator-owned loopback FastMCP HTTP host
- per-run access is enforced by registered bindings
- requests identify the active binding with `X-Vibrant-Binding`
- the older bearer-token transport notes are no longer the source of truth

## Prerequisites

- dependencies installed with `uv`
- MCP extras installed when needed:

```bash
uv sync --extra mcp --dev
```

Use a disposable project root for transport tests so stale local `.vibrant/`
state does not affect the results.

## Recommended Automated Tests

The current transport and binding behavior is covered by:

- `tests/test_orchestrator_mcp_transport.py`
- `tests/test_provider_invocation_compiler.py`
- `tests/test_orchestrator_mcp_surface.py`

Example targeted runs:

```bash
uv run pytest -q tests/test_orchestrator_mcp_transport.py
uv run pytest -q tests/test_provider_invocation_compiler.py
uv run pytest -q tests/test_orchestrator_mcp_surface.py
```

What these verify:

- the loopback FastMCP host exposes the expected tool and resource surface
- worker bindings are filtered server-side
- provider invocation plans include the MCP endpoint and per-run binding
  headers

## Manual Inspection Strategy

The simplest current manual path is to exercise the embedded host in tests or a
small local harness rather than the older standalone dev-server flow.

Manual checks should confirm:

- the endpoint is loopback HTTP, typically `http://127.0.0.1:<port>/mcp`
- requests without `X-Vibrant-Binding` are rejected
- Gatekeeper bindings expose semantic write tools such as
  `vibrant.add_task` and `vibrant.update_consensus`
- worker bindings expose only the narrower read surface they are allowed to use
- resources include `vibrant.get_workflow_session` so the orchestrator-owned
  session and concurrency limit are visible to MCP consumers

## Current Permission Model

The current permission model is binding-based.

Current behavior:

- the host is loopback-only
- each run registers a binding id
- the binding id is sent in `X-Vibrant-Binding`
- the server resolves that binding to a principal plus allowed tools and
  resources
- visibility is enforced server-side, not only by provider launch config

## Transport Notes

Provider launch integration currently works like this:

- the binding layer produces a provider-neutral access descriptor
- the provider invocation compiler turns that into provider-native launch args
- the resulting plan injects the loopback MCP endpoint and static binding
  header for that run

This is why MCP transport docs should talk about bindings and invocation plans,
not just raw HTTP reachability.
