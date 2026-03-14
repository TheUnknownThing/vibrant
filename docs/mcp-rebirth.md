# MCP Rebirth Proposal

Status: proposed
Date: 2026-03-13

## Summary

Vibrant should move from an in-process MCP registry to a real FastMCP-based
orchestrator server that runs on loopback HTTP and is consumed by Codex through
per-run Codex config overrides.

The target design is:

- keep the current semantic orchestrator MCP handlers as the authority layer
- add a FastMCP transport adapter on top of that authority layer
- expose the server on `127.0.0.1` only
- do not configure OAuth or any other FastMCP auth flow
- let the orchestrator binding layer decide which tools and resources each agent
  session can see
- inject the relevant MCP server configuration into Codex for that specific run
  instead of relying on a shared static `.codex/config.toml`

This aligns with the orchestrator redesign in
[`docs/orchestrator-rebirth.md`](/home/color/workspace/vibrant/docs/orchestrator-rebirth.md)
while removing the separate auth scaffolding that is currently not wired into
the orchestrator runtime.

## External Constraints

These constraints come from the current upstream docs and should drive the
design rather than local assumptions.

### Codex configuration

Official Codex docs currently say:

- project and user config live in `~/.codex/config.toml` and
  `.codex/config.toml`
- CLI flags and `--config` overrides have higher precedence than config files
- `--config` accepts `key=value` where the value is parsed as TOML
- dot notation can target nested keys such as `mcp_servers.context7.enabled`
- MCP server config supports `mcp_servers.<id>.url`,
  `mcp_servers.<id>.enabled`, `mcp_servers.<id>.enabled_tools`,
  `mcp_servers.<id>.disabled_tools`, `mcp_servers.<id>.http_headers`, and
  `mcp_servers.<id>.required`
- identity policy can distinguish stdio MCP servers from streamable HTTP MCP
  servers using either `command` or `url`

Implication:

- per-run MCP configuration should be injected into Codex with CLI config
  overrides rather than by mutating shared config files

### FastMCP transport

FastMCP docs currently say:

- tools and resources are exposed directly from Python functions
- HTTP transport is the Streamable HTTP transport
- HTTP is the recommended transport for network-based deployments
- auth is optional and only relevant when the server is actually configured to
  enforce it

Implication:

- Vibrant should use FastMCP HTTP transport on loopback, not STDIO
- no auth provider should be configured for the first version

## Current State

Today the repository already has a meaningful MCP authority layer, but not a
real MCP transport.

### What already exists

- [`vibrant/orchestrator/mcp/server.py`](/home/color/workspace/vibrant/vibrant/orchestrator/mcp/server.py)
  contains a typed semantic MCP registry
- [`vibrant/orchestrator/mcp/tools.py`](/home/color/workspace/vibrant/vibrant/orchestrator/mcp/tools.py)
  and
  [`vibrant/orchestrator/mcp/resources.py`](/home/color/workspace/vibrant/vibrant/orchestrator/mcp/resources.py)
  already map high-level operations onto orchestrator stores and services
- [`vibrant/orchestrator/binding.py`](/home/color/workspace/vibrant/vibrant/orchestrator/binding.py)
  already models per-agent capability bindings
- bootstrap already creates both the semantic MCP server and the binding service

### What is missing

- the current MCP server is only an in-process dispatcher, not a networked MCP
  endpoint
- Gatekeeper and worker launches do not call the binding service before runtime
  startup
- the binding metadata is not passed into Codex launch or Codex thread config
- the separate `vibrant/mcp` auth package is not the active orchestrator MCP
  path

## Design Goals

- preserve the orchestrator as the single authority for durable workflow state
- make MCP the real Gatekeeper mutation path, not prompt-only guidance
- avoid any OAuth flow because the server is localhost-only
- avoid a shared global MCP profile that leaks Gatekeeper-only tools into other
  runs
- keep compatibility with the current semantic tool and resource names while the
  transport layer is introduced

## Non-Goals

- no public remote MCP deployment
- no browser-based OAuth login
- no attempt to make workers fully autonomous MCP writers in the first phase
- no transcript parsing fallback as an authority path

## Proposed Architecture

## 1. Keep the current semantic MCP layer as the backend

The existing orchestrator MCP code should remain the semantic authority layer.
It already matches the redesign well enough:

- `vibrant.get_*` style read resources
- typed workflow, roadmap, consensus, question, and review tools
- compatibility aliases that can be retired later

This layer should continue to own:

- argument validation
- mapping to orchestrator command handlers
- serialization into plain values
- permission checks based on the binding context

The major change is that it should stop pretending to be the transport.

## 2. Add a FastMCP transport host in `vibrant/orchestrator/mcp`

Add a new server host layer, for example:

- `fastmcp_host.py`
- `binding_registry.py`
- `transport.py`

Responsibilities:

- construct a FastMCP server instance
- register the existing orchestrator tools and resources with FastMCP
- expose the server over loopback HTTP only
- manage the server lifecycle with the orchestrator lifecycle
- resolve the binding context for each incoming request

The existing `OrchestratorMCPServer` can either keep its current name for
backward compatibility or be treated internally as the semantic registry that
the FastMCP host delegates to.

## 3. Use Streamable HTTP on loopback

Run the FastMCP server as an in-process HTTP service:

- host: `127.0.0.1`
- port: orchestrator-managed fixed or ephemeral port
- endpoint: FastMCP default MCP endpoint

Reasoning:

- Codex natively understands HTTP MCP configuration
- FastMCP recommends HTTP for network-based use
- one server can support multiple concurrent agent sessions
- Vibrant avoids subprocess MCP proxy layers and keeps the orchestrator object
  in memory where it already lives

## 4. Do not use OAuth or any other auth flow

Delete the current embedded auth direction instead of carrying it as dead code.

The proposal is explicitly:

- no `AuthorizationServerService`
- no auth FastAPI app
- no JWT minting
- no OAuth scopes or login flow
- no FastMCP auth provider

The server is loopback-only and bound to the local machine. The remaining
access-control problem is not identity verification. It is capability routing.

## 5. Keep capability routing in the binding layer

Removing auth does not remove the need for capability separation.

The orchestrator still needs to keep:

- Gatekeeper tools visible to Gatekeeper
- worker access narrower than Gatekeeper
- future validator or merge-agent access even narrower

`AgentSessionBindingService` should become the owner of the effective MCP
binding for a run. Its output should evolve from a descriptive object into a
runtime input that can be consumed by the Codex launcher and by the FastMCP
server.

Each binding should contain at least:

- a `binding_id`
- visible tool names
- visible resource names
- the Codex MCP server id to inject for that run
- any transport metadata needed by Codex, such as URL and static headers

## 6. Enforce bindings server-side, not only in Codex config

Codex-side tool filtering is useful, but it is not enough.

The FastMCP server should enforce the effective binding itself. The simplest
proposal is:

- every run gets a `binding_id`
- Codex is configured to connect to the same loopback FastMCP host
- Codex also sends a static binding header such as `X-Vibrant-Binding`
- the FastMCP host resolves that binding id to an allowed tool/resource set
- any tool or resource outside the binding is hidden or rejected

Why this is necessary:

- `enabled_tools` and `disabled_tools` only protect the client side
- resources need the same restriction model
- the orchestrator should not trust the client to faithfully enforce the
  intended capability surface

## 7. Configure Codex per run, not per shared profile

The orchestrator should inject a run-specific Codex MCP profile using CLI
config overrides, not by mutating a shared `.codex/config.toml`.

For the Gatekeeper run, the orchestrator should generate Codex overrides such
as:

- enable an MCP server entry for the orchestrator host
- point that MCP server at the loopback FastMCP URL
- set the enabled tool allowlist for that run
- attach static HTTP headers needed to resolve the binding
- mark the server required if failure should block the run

The exact CLI plumbing is described in
[`docs/provider-arg-append.md`](/home/color/workspace/vibrant/docs/provider-arg-append.md).

## 8. Gatekeeper first, workers later

The first live consumer should be the Gatekeeper only.

Reasoning:

- the redesign explicitly makes Gatekeeper the control-plane authority
- workers currently have no active MCP write path
- getting the Gatekeeper onto real MCP is enough to validate the entire bridge
  architecture

Phase 1 worker behavior can remain unchanged.

Possible follow-up states:

- workers get no MCP access
- workers get read-only resources
- specific worker types get very narrow write tools later

## Proposed File-Level Changes

### Additions

- `vibrant/orchestrator/mcp/fastmcp_host.py`
- `vibrant/orchestrator/mcp/binding_registry.py`
- transport lifecycle glue in orchestrator bootstrap
- transport-oriented tests for the new host

### Modifications

- `vibrant/orchestrator/binding.py`
- `vibrant/orchestrator/bootstrap.py`
- `vibrant/orchestrator/gatekeeper/lifecycle.py`
- later, `vibrant/orchestrator/execution/coordinator.py`

### Deletions

Delete the current auth scaffolding:

- `vibrant/mcp/auth/`
- `vibrant/mcp/authz.py`
- `vibrant/mcp/config.py`
- `docs/embedded_auth.md`
- auth-specific tests under `tests/`

If `vibrant/mcp/__init__.py` becomes meaningless after that deletion, remove or
shrink it rather than preserving a misleading package boundary.

## Rollout Plan

### Phase 1: transport MVP

- keep the current semantic backend intact
- add a FastMCP host
- expose a small subset of orchestrator tools and resources
- connect only the Gatekeeper path
- verify a real Gatekeeper run can call the MCP server

### Phase 2: full Gatekeeper MCP surface

- expose the full current semantic tool and resource set
- resolve binding visibility with a binding id
- enforce visibility server-side
- keep compatibility aliases temporarily

### Phase 3: cleanup

- delete embedded auth code and tests
- remove prompt language that says MCP is available only "when the bridge is
  available"
- retire transitional semantic aliases after the live bridge is proven

### Phase 4: worker policy

- decide whether workers get no MCP, read-only MCP, or narrow write access
- wire worker binding only after Gatekeeper MCP is stable

## Risks

### Binding enforcement design

The main design decision is how to identify the binding in each HTTP request.
The current proposal uses static headers because:

- Codex config already supports static `http_headers`
- this does not require OAuth
- one FastMCP host can still serve multiple runs

An alternative would be a unique endpoint path or a unique loopback port per
binding. That is simpler in some ways, but increases server lifecycle churn.

### Compatibility debt

The current semantic MCP layer still exposes compatibility aliases. If they are
kept too long, the new transport will harden the wrong contract. The transport
rollout should happen before large new compatibility surface is added.

### Shared-server assumptions

Once the server is real and long-lived, lifecycle details matter:

- port allocation
- restart behavior
- cleanup on orchestrator exit
- stale binding cleanup

Those details should be part of the transport host, not pushed into provider
adapters or the Gatekeeper lifecycle service.

## Recommendation

Implement the FastMCP bridge as:

- one in-process FastMCP HTTP server per orchestrator instance
- loopback-only transport
- no auth provider
- per-run binding resolution via static headers
- Codex per-run MCP configuration injected through CLI config overrides

That gives Vibrant a real MCP architecture without adding an auth layer that
the current product does not need.

## Sources

- OpenAI Codex Config Basics:
  <https://developers.openai.com/codex/config-basic>
- OpenAI Codex Advanced Config:
  <https://developers.openai.com/codex/config-advanced>
- OpenAI Codex Config Reference:
  <https://developers.openai.com/codex/config-reference>
- FastMCP Welcome:
  <https://gofastmcp.com/getting-started/welcome>
- FastMCP Running Your Server:
  <https://gofastmcp.com/deployment/running-server>
- FastMCP Authorization:
  <https://gofastmcp.com/servers/authorization>
