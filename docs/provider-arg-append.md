# Provider Argument Transformation Plan

Status: proposed
Date: 2026-03-13

## Summary

Vibrant needs an explicit middle layer that transforms provider-neutral MCP
binding information into provider-specific launch and session arguments.

This is not only a Codex problem.

The immediate trigger is Codex MCP support, because Codex can accept per-run
config overrides through repeated `--config key=value`. But the same
orchestrator binding must also be able to target other providers, including
Claude, whose runtime is configured through SDK options rather than the Codex
CLI.

The correct architecture is:

- the orchestrator produces one normalized MCP binding description
- a dedicated provider-argument compiler converts that description into the
  exact launch/session shape required by the active provider
- the runtime launches the provider using that compiled plan

This document describes the target design for that middle layer.

## Problem Statement

The current runtime stack mixes three concerns that should be separated:

- semantic capability selection
- provider launch construction
- provider-specific transport details

That is already becoming a problem for Codex, and it will become worse once
Vibrant supports more than one provider-specific MCP integration path.

### Current failure mode

Today the repository has most of the pieces, but they stop short of a usable
end-to-end path.

- `launch_args` exists in
  [`vibrant/config.py`](/home/color/workspace/vibrant/vibrant/config.py#L49),
  but it is not passed through the main agent launch path in
  [`vibrant/agents/base.py`](/home/color/workspace/vibrant/vibrant/agents/base.py#L232).
- the Codex transport can consume launch args in
  [`vibrant/providers/codex/adapter.py`](/home/color/workspace/vibrant/vibrant/providers/codex/adapter.py#L31)
  and
  [`vibrant/providers/codex/client.py`](/home/color/workspace/vibrant/vibrant/providers/codex/client.py#L69),
  but real runs never receive them
- the orchestrator binding layer already computes `provider_binding` metadata in
  [`vibrant/orchestrator/binding.py`](/home/color/workspace/vibrant/vibrant/orchestrator/binding.py#L66),
  but neither Gatekeeper startup nor worker startup consumes it
- the only generic pass-through that reaches Codex today is `extra_config`,
  which is merged into the thread payload in
  [`vibrant/providers/codex/adapter.py`](/home/color/workspace/vibrant/vibrant/providers/codex/adapter.py#L536)
  and
  [`vibrant/providers/codex/adapter.py`](/home/color/workspace/vibrant/vibrant/providers/codex/adapter.py#L552)

That last point is useful, but it is not enough. Provider-neutral binding data
must not be shoved directly into a provider thread payload and treated as if all
providers consume the same shape.

## Why a Middle Layer Is Required

Codex and Claude already show that provider configuration is structurally
different.

### Codex

Codex launch is process-oriented.

- the app is launched as `codex app-server`
- per-run config overrides are naturally expressed as CLI arguments
- MCP server configuration belongs to Codex config keys such as
  `mcp_servers.<id>.*`
- some settings are process-level and some are thread-level

### Claude

Claude launch is SDK-oriented.

- the provider is initialized with `ClaudeAgentOptions`
- tool allow/deny policy is expressed as SDK options like `allowed_tools` and
  `disallowed_tools`
- extra provider behavior is passed through option fields such as `env`,
  `plugins`, `agents`, and related keys in
  [`vibrant/providers/claude/adapter.py`](/home/color/workspace/vibrant/vibrant/providers/claude/adapter.py#L155)
- there is no natural equivalent of Codex `--config mcp_servers.*`

### Consequence

The orchestrator should never compile its MCP binding directly into Codex CLI
flags or Claude SDK options. That translation must happen in a dedicated
provider-specific compiler layer.

Otherwise the codebase ends up with:

- Codex-specific fields leaking into orchestrator binding code
- provider adapters carrying policy they do not own
- no reusable place to express "this agent gets these MCP capabilities" once
  more providers are added

## Design Goals

- keep the orchestrator binding output provider-neutral
- support different provider launch shapes without duplicating orchestration
  policy
- keep provider adapters focused on provider protocol details, not orchestration
  policy
- make per-run provider configuration explicit and inspectable
- preserve a clean boundary between process launch settings and thread/session
  settings

## Non-Goals

- this document does not specify FastMCP server internals
- this document does not specify provider transport protocol details beyond what
  is needed for argument shaping
- this document does not propose a migration strategy or staged compatibility
  plan

## Proposed Architecture

## 1. Normalize the binding output

`AgentSessionBindingService` should output a provider-neutral binding descriptor,
not provider-specific arguments.

Example:

```python
@dataclass(slots=True)
class MCPBindingDescriptor:
    binding_id: str
    role: str
    conversation_id: str | None
    session_id: str
    tool_names: list[str]
    resource_names: list[str]
    transport_hint: Literal["http", "stdio"] | None = None
    endpoint_url: str | None = None
    required: bool = True
    static_headers: dict[str, str] = field(default_factory=dict)
```

This object says what the run should be allowed to access. It does not say how
Codex or Claude should consume that information.

Responsibilities of the binding layer:

- choose visible tools and resources
- assign a stable `binding_id`
- point the binding at the orchestrator MCP endpoint
- express provider-neutral transport intent

Responsibilities the binding layer should not own:

- rendering CLI flags
- rendering Codex config overrides
- constructing Claude SDK option dictionaries

## 2. Add an explicit provider-argument compiler layer

Add a new provider-facing middle layer with an interface like:

```python
@dataclass(slots=True)
class ProviderInvocationPlan:
    launch_env: dict[str, str] = field(default_factory=dict)
    launch_args: list[str] = field(default_factory=list)
    session_options: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class ProviderArgumentCompiler(Protocol):
    def compile(
        self,
        *,
        provider_kind: ProviderKind,
        project_config: VibrantConfig,
        binding: MCPBindingDescriptor | None,
        agent_record: AgentRecord,
    ) -> ProviderInvocationPlan: ...
```

This layer is the translation seam the current architecture is missing.

Why this should exist as its own subsystem:

- it centralizes provider-specific compilation logic
- it keeps binding policy out of the providers
- it gives the runtime one stable input regardless of provider
- it allows later providers to be added without rewriting orchestrator policy

## 3. Make the runtime consume `ProviderInvocationPlan`

The runtime start boundary should accept a compiled invocation plan, not a loose
set of optional provider kwargs.

Example:

```python
async def start_run(
    ...,
    invocation_plan: ProviderInvocationPlan | None = None,
) -> AgentHandle: ...
```

The same applies to `resume_run()`.

`AgentBase` should then merge:

- project-level provider config
- agent-type defaults
- compiled invocation plan

before constructing the provider adapter.

This makes launch behavior explicit and inspectable.

## 4. Split process launch config from thread/session config

The middle layer should not collapse everything into one dictionary.

The minimum split is:

- `launch_env`
- `launch_args`
- `session_options`

Why:

- Codex needs process launch args for `--config`
- Claude needs SDK options, not CLI args
- both may still need session-level options beyond process launch

This split also makes debugging much simpler.

## Provider-Specific Compilation

## 1. Codex compilation

For Codex, the provider-argument compiler should transform
`MCPBindingDescriptor` into:

- repeated `--config key=value` launch arguments
- optional environment variables such as `CODEX_HOME`
- optional thread/session payload augmentation when appropriate

Example target shape:

```text
codex \
  --config 'mcp_servers.vibrant.enabled=true' \
  --config 'mcp_servers.vibrant.url="http://127.0.0.1:8765/mcp"' \
  --config 'mcp_servers.vibrant.enabled_tools=["vibrant.add_task","vibrant.update_task_definition"]' \
  --config 'mcp_servers.vibrant.required=true' \
  --config 'mcp_servers.vibrant.http_headers={ "X-Vibrant-Binding" = "binding-gk-123" }' \
  app-server
```

Codex-specific observations:

- the Codex docs already support this one-off config model
- this is a process launch concern, not a thread payload concern
- `extra_config` should not be the primary mechanism for dynamic MCP server
  registration

The Codex compiler should be responsible for:

- producing a stable server id
- rendering TOML-safe `--config` values
- deciding when `required=true` should be set
- passing static binding headers if the orchestrator MCP host uses them

## 2. Claude compilation

For Claude, the compiler should not try to mimic the Codex CLI model.

Instead, it should compile the same binding into Claude-specific session
options. Based on the current adapter structure, that may include:

- `allowed_tools`
- `disallowed_tools`
- provider `plugins`
- provider `agents`
- provider `env`
- other supported Claude SDK options routed through `claude_extra_config`

Relevant current seams already exist in:

- [`vibrant/agents/base.py`](/home/color/workspace/vibrant/vibrant/agents/base.py#L237)
- [`vibrant/providers/claude/adapter.py`](/home/color/workspace/vibrant/vibrant/providers/claude/adapter.py#L71)
- [`vibrant/providers/claude/adapter.py`](/home/color/workspace/vibrant/vibrant/providers/claude/adapter.py#L155)

The Claude compiler should decide how MCP access is represented for Claude
without forcing the orchestrator binding layer to know those details.

That means the compiler may eventually produce:

- no MCP-specific options if Claude cannot consume them directly
- a plugin/agent bridge descriptor if Claude supports that path
- only tool policy changes for Claude while Codex gets a full MCP server config

The important point is that the architecture can represent those differences
cleanly.

## 3. Future providers

New providers should implement the same compiler contract.

That means adding a provider is:

- define how that provider consumes launch/session config
- add one compiler implementation
- keep the orchestrator binding policy unchanged

This is much cleaner than threading new provider-specific fields through
`AgentBase` every time.

## Concrete Model Proposal

The following shape is sufficient for the current need while remaining
provider-neutral:

```python
@dataclass(slots=True)
class ProviderInvocationPlan:
    provider_kind: ProviderKind
    launch_env: dict[str, str] = field(default_factory=dict)
    launch_args: list[str] = field(default_factory=list)
    session_options: dict[str, Any] = field(default_factory=dict)
    visible_tools: list[str] = field(default_factory=list)
    visible_resources: list[str] = field(default_factory=list)
    binding_id: str | None = None
    debug_metadata: dict[str, Any] = field(default_factory=dict)
```

This should be the runtime input.

The provider compiler should also have access to the project config so it can
merge defaults rather than replacing them.

## Current Code Impact

## `vibrant/orchestrator/binding.py`

Change responsibility from:

- producing descriptive `provider_binding` blobs

to:

- producing `MCPBindingDescriptor`
- remaining the single policy owner for capability selection

It should not produce raw Codex `--config` flags.

## `vibrant/agents/base.py`

Change responsibility from:

- manually threading a fixed list of provider kwargs

to:

- accepting a compiled `ProviderInvocationPlan`
- applying the plan when constructing the adapter
- passing provider launch details in a provider-neutral way

This is the narrowest place where all providers already converge.

## `vibrant/providers/codex/client.py`

The client should stop treating `launch_args` as a replacement argv tail.

It should explicitly support:

- appending launch args before `app-server`
- receiving repeated `--config` arguments
- consuming launch env from the invocation plan

The resulting command shape should always include `app-server`.

## `vibrant/providers/codex/adapter.py`

The adapter should receive Codex-relevant parts of the invocation plan, not
reconstruct policy on its own.

Responsibilities:

- pass compiled CLI flags and env to the client
- preserve the existing thread payload logic where appropriate
- avoid becoming the owner of orchestrator binding semantics

## `vibrant/providers/claude/adapter.py`

The adapter already demonstrates that provider config is structurally different
from Codex.

It should consume only the Claude-relevant part of the invocation plan, such
as:

- tool allowlists and deny lists
- plugin or agent descriptors
- Claude SDK extra config

It should not know how to interpret orchestrator MCP binding policy directly.

## `vibrant/agents/runtime.py`

The runtime service should accept the compiled invocation plan and preserve it
through both `start_run()` and `resume_run()`.

That keeps provider-launch concerns out of orchestrator lifecycle services and
lets those services pass one explicit object instead of ad hoc kwargs.

## `vibrant/orchestrator/gatekeeper/lifecycle.py`

Before launching the Gatekeeper:

- call the binding layer
- compile the binding through the provider-argument compiler
- pass the resulting invocation plan into the runtime

The lifecycle service should not know whether the active provider is using:

- Codex `--config`
- Claude SDK options
- some future provider-specific representation

## `vibrant/orchestrator/execution/coordinator.py`

Use the same mechanism for worker runs once worker MCP policy is ready.

The worker path should not get a separate provider-config mechanism.

## Configuration Policy

Configuration precedence should be:

1. provider/client built-in defaults
2. project `VibrantConfig`
3. agent-type defaults
4. compiled invocation plan for this run

This preserves a clean layering:

- project config expresses long-lived defaults
- orchestrator binding expresses run-specific capability policy
- provider compilers translate that policy into launch/session details

## Persistence and Debugging

The effective invocation plan should be represented in agent metadata well
enough to debug a run.

Do not persist raw secrets if they ever appear, but do persist enough to answer:

- which binding id was used
- which provider compiler produced the plan
- which MCP server id or equivalent provider object was used
- what endpoint URL was targeted
- what tool/resource visibility the run was supposed to have

That metadata belongs in a normalized form, not as a raw dump of argv tokens.

## Example End State

Gatekeeper binding flow:

1. `AgentSessionBindingService` produces `MCPBindingDescriptor`
2. `ProviderArgumentCompilerRegistry` picks the compiler for the active provider
3. the compiler produces `ProviderInvocationPlan`
4. the runtime launches the provider with that plan
5. the provider adapter consumes only its translated provider-specific inputs

Codex result:

- loopback FastMCP endpoint wired through repeated `--config`

Claude result:

- equivalent capability restrictions and provider-specific tool/session options
  without pretending Claude consumes Codex config

## Recommendation

Do not solve this by:

- passing more loose kwargs through `AgentBase`
- embedding Codex-specific config generation in the orchestrator binding layer
- overloading `extra_config` with provider launch semantics

Do solve it by adding:

- one normalized `MCPBindingDescriptor`
- one explicit `ProviderArgumentCompiler` layer
- one runtime-level `ProviderInvocationPlan`

That is the minimum architecture that can support Codex now and other providers
later without turning MCP integration into provider-specific glue scattered
across the orchestrator.

## Sources

- OpenAI Codex Config Basics:
  <https://developers.openai.com/codex/config-basic>
- OpenAI Codex Advanced Config:
  <https://developers.openai.com/codex/config-advanced>
- OpenAI Codex Config Reference:
  <https://developers.openai.com/codex/config-reference>
