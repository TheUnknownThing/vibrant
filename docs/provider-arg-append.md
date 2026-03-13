# Provider Argument Transformation Plan

Status: proposed
Date: 2026-03-13

## Summary

Vibrant needs an explicit middle layer that transforms provider-neutral MCP
binding information into provider-specific launch and session arguments.

This is not only a Codex problem.

The immediate trigger is orchestrator MCP support for Codex, because Codex can
accept per-run config overrides through repeated `--config key=value`. But the
same orchestrator binding must also be able to target other providers,
including Claude, whose runtime is configured through SDK options rather than a
CLI.

The target architecture is:

- the orchestrator binding layer produces one normalized MCP access descriptor
- a provider-argument compiler converts that descriptor into the exact provider
  invocation shape required by the active provider
- the runtime launches the provider using that compiled invocation plan

The important constraint is that orchestration policy must stay provider-
neutral. Codex-specific flags and Claude-specific option maps should be emitted
only by the compiler layer, not by the orchestrator itself.

## Problem

The current runtime stack mixes three different concerns:

- semantic capability selection
- provider launch construction
- provider-specific transport details

That is already a problem for Codex, and it will become a larger design problem
once Vibrant supports more providers with different MCP integration models.

### Verified current gaps

- `launch_args` exists in
  [`vibrant/config.py`](/home/color/workspace/vibrant/vibrant/config.py#L49),
  but the main launch path in
  [`vibrant/agents/base.py`](/home/color/workspace/vibrant/vibrant/agents/base.py#L232)
  never passes it into `adapter_factory(...)`
- the Codex transport can consume launch args in
  [`vibrant/providers/codex/adapter.py`](/home/color/workspace/vibrant/vibrant/providers/codex/adapter.py#L31)
  and
  [`vibrant/providers/codex/client.py`](/home/color/workspace/vibrant/vibrant/providers/codex/client.py#L69),
  but real runs never receive them
- the orchestrator binding layer already computes descriptive `provider_binding`
  metadata in
  [`vibrant/orchestrator/binding.py`](/home/color/workspace/vibrant/vibrant/orchestrator/binding.py#L66),
  but neither Gatekeeper startup nor worker startup consumes it before provider
  launch
- the only generic pass-through that reaches Codex today is `extra_config`,
  merged into the thread payload in
  [`vibrant/providers/codex/adapter.py`](/home/color/workspace/vibrant/vibrant/providers/codex/adapter.py#L536)
  and
  [`vibrant/providers/codex/adapter.py`](/home/color/workspace/vibrant/vibrant/providers/codex/adapter.py#L552)

That last seam is useful, but it is the wrong abstraction boundary for
provider-neutral MCP policy. It is a provider thread payload escape hatch, not
a general provider invocation model.

## Why a Middle Layer Is Required

Codex and Claude already show that provider configuration is structurally
different.

### Codex

Codex launch is process-oriented.

- the app is launched as `codex app-server`
- one-off provider configuration is naturally expressed as CLI arguments
- MCP server configuration belongs to Codex config keys such as
  `mcp_servers.<id>.*`
- some settings are process-level while others are part of the thread payload

### Claude

Claude launch is SDK-oriented.

- the provider is initialized with `ClaudeAgentOptions`
- tool policy is expressed as SDK options such as `allowed_tools` and
  `disallowed_tools`
- extra provider behavior is passed through option fields like `env`, `plugins`,
  and `agents` in
  [`vibrant/providers/claude/adapter.py`](/home/color/workspace/vibrant/vibrant/providers/claude/adapter.py#L155)
- there is no natural equivalent of Codex `--config mcp_servers.*`

### Consequence

The orchestrator must not compile its MCP binding directly into:

- Codex CLI flags
- Codex config override strings
- Claude SDK option dictionaries
- any other provider-native transport shape

That translation belongs in a dedicated compiler layer.

Without that layer, the codebase will drift toward:

- Codex-specific fields leaking into orchestrator policy code
- provider adapters becoming policy owners
- repeated ad hoc launch plumbing for every new provider

## Design Goals

- keep orchestrator binding output provider-neutral
- preserve one authoritative place where MCP capability policy is decided
- support different provider launch shapes without duplicating orchestration
  policy
- keep provider adapters focused on provider protocol behavior, not binding
  policy
- make per-run provider configuration explicit, inspectable, and persistable
- separate process launch settings from thread/session settings

## Non-Goals

- this document does not define FastMCP server internals
- this document does not define provider protocol semantics beyond the argument
  transformation boundary
- this document does not specify backward-compatibility shims or staged
  migration behavior

## Target Architecture

The design should be expressed as three distinct layers.

### Layer 1: binding policy

Owner:

- `AgentSessionBindingService`

Responsibility:

- decide what the agent is allowed to access

Output:

- one provider-neutral MCP access descriptor

This layer answers:

- which tools are visible
- which resources are visible
- which MCP endpoint should be used
- whether the binding is required
- which stable identity should represent the binding

This layer must not answer:

- how Codex receives that binding
- how Claude receives that binding
- which CLI flags or SDK options are required

### Layer 2: provider-argument compilation

Owner:

- a provider compiler subsystem, one compiler per provider family

Responsibility:

- translate the provider-neutral binding into provider-native launch and session
  arguments

Output:

- one provider invocation plan

This layer answers:

- what environment variables should be set
- what process arguments should be passed
- what provider session options should be used
- what normalized debug metadata should be persisted

### Layer 3: runtime launch

Owner:

- the runtime and provider adapter boundary

Responsibility:

- consume the compiled invocation plan and start or resume the provider

This layer should not reinterpret MCP policy. It should only execute the plan
it receives.

## Data Contracts

## 1. Provider-neutral binding descriptor

The binding layer should emit an explicit, provider-neutral descriptor.

Example:

```python
@dataclass(slots=True)
class MCPAccessDescriptor:
    binding_id: str
    role: str
    session_id: str
    conversation_id: str | None
    visible_tools: list[str]
    visible_resources: list[str]
    endpoint_url: str | None
    transport_hint: Literal["http", "stdio"] | None = None
    required: bool = True
    static_headers: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
```

Required properties of this descriptor:

- it is valid even when the provider is not Codex
- it can describe read-only and write-capable bindings
- it can represent different visibility surfaces for Gatekeeper, workers,
  validators, and future agent roles
- it does not expose provider-native argument syntax

## 2. Provider invocation plan

The provider compiler should emit one runtime-facing invocation plan.

Example:

```python
@dataclass(slots=True)
class ProviderInvocationPlan:
    provider_kind: ProviderKind
    launch_env: dict[str, str] = field(default_factory=dict)
    launch_args: list[str] = field(default_factory=list)
    session_options: dict[str, Any] = field(default_factory=dict)
    binding_id: str | None = None
    visible_tools: list[str] = field(default_factory=list)
    visible_resources: list[str] = field(default_factory=list)
    debug_metadata: dict[str, Any] = field(default_factory=dict)
```

This object is the runtime input.

It should be rich enough to:

- launch Codex with repeated `--config` overrides
- launch Claude with SDK options and tool policy
- support future providers with different transport models

It should also be normalized enough that the runtime can treat it as one stable
contract instead of a provider-specific kwargs bag.

## 3. Compiler contract

The missing middle layer should be explicit.

Example:

```python
class ProviderArgumentCompiler(Protocol):
    def compile(
        self,
        *,
        provider_kind: ProviderKind,
        project_config: VibrantConfig,
        agent_record: AgentRecord,
        binding: MCPAccessDescriptor | None,
    ) -> ProviderInvocationPlan: ...
```

There should also be a registry or resolver that selects the correct compiler
for the active provider.

That registry is important because:

- orchestrator code should not switch on provider type in multiple places
- adding a provider should mean adding one compiler, not threading new special
  cases through the runtime

## Ownership Boundaries

## `AgentSessionBindingService`

Owns:

- capability policy
- visibility sets
- stable binding identity
- transport intent in provider-neutral form

Must not own:

- Codex `--config` rendering
- Codex argv construction
- Claude SDK option shaping

## Provider compilers

Own:

- provider-specific translation
- process versus session split for that provider
- provider-native argument syntax

Must not own:

- selection of visible tools or resources
- orchestration policy about what a role is allowed to do

## Runtime and provider adapters

Own:

- launching the provider
- resuming the provider
- passing compiled data to the provider in the right mechanical form

Must not own:

- orchestration capability policy
- provider-specific translation policy that belongs in the compiler

## Provider-Specific Projections

## 1. Codex projection

For Codex, the provider compiler should translate `MCPAccessDescriptor` into:

- repeated `--config key=value` launch arguments
- optional launch environment variables such as `CODEX_HOME`
- optional thread/session options when they are truly thread-scoped

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

- this is primarily a process launch concern
- MCP registration should not be modeled as a generic thread payload patch
- Codex config precedence makes per-run overrides the correct fit for
  orchestrator-driven bindings

The Codex compiler should therefore own:

- server id generation
- TOML-safe config rendering
- the split between CLI config overrides and thread/session options
- any static header injection needed for binding selection on loopback HTTP

## 2. Claude projection

For Claude, the provider compiler should not attempt to imitate the Codex CLI
model.

Instead, it should project the same `MCPAccessDescriptor` into Claude-native
session options, for example:

- `allowed_tools`
- `disallowed_tools`
- provider plugin descriptors
- provider agent descriptors
- provider environment settings
- other supported SDK options routed into `claude_extra_config`

Relevant current seams already exist in:

- [`vibrant/agents/base.py`](/home/color/workspace/vibrant/vibrant/agents/base.py#L237)
- [`vibrant/providers/claude/adapter.py`](/home/color/workspace/vibrant/vibrant/providers/claude/adapter.py#L71)
- [`vibrant/providers/claude/adapter.py`](/home/color/workspace/vibrant/vibrant/providers/claude/adapter.py#L155)

The important point is not that Claude must consume MCP in the same way as
Codex. The important point is that the architecture must give Claude an equally
clean target for provider-native argument generation.

That means the Claude compiler may legitimately produce:

- no direct MCP transport config
- only tool policy changes
- plugin or bridge descriptors if Claude later gains an MCP bridge path

## 3. Future providers

Future providers should implement the same compiler contract.

Adding a provider should mean:

- define how that provider consumes launch and session config
- add one compiler implementation
- keep orchestrator capability policy unchanged

That is the core payoff of the middle layer.

## Runtime Integration

The runtime boundary should consume `ProviderInvocationPlan` directly.

Example:

```python
async def start_run(
    ...,
    invocation_plan: ProviderInvocationPlan | None = None,
) -> AgentHandle: ...
```

The same applies to `resume_run()`.

The runtime should:

- merge project-level defaults with the compiled plan
- pass the compiled launch and session data into the adapter constructor and
  startup path
- avoid introducing new provider-specific branches outside the compiler and
  adapter layers

This is the seam that keeps lifecycle services provider-neutral.

## Configuration Precedence

The correct precedence order is:

1. provider/client built-in defaults
2. project `VibrantConfig`
3. agent-type defaults
4. compiled invocation plan for this run

This gives the design a clean layering:

- project config defines durable defaults
- the binding layer defines run-scoped capability policy
- the provider compiler defines provider-native projection
- the runtime executes the resulting plan

## Persistence and Debugging

The effective invocation plan should be represented in durable metadata well
enough to explain a run after the fact.

The important persisted information is:

- binding id
- provider kind
- compiler identity or compiler family
- effective endpoint URL or equivalent provider-side bridge target
- visible tools and resources
- normalized provider launch metadata, such as configured server id

The goal is not to persist raw argv strings. The goal is to persist the meaning
of the chosen invocation plan in a provider-neutral or lightly normalized form.

## Codebase Impact

## `vibrant/orchestrator/binding.py`

Refocus this file on capability policy only.

It should:

- produce `MCPAccessDescriptor`
- remain the single owner of tool and resource visibility policy

It should not:

- render Codex config strings
- build provider-native option maps

## `vibrant/agents/base.py`

Refocus this file on runtime handoff.

It should:

- accept a compiled `ProviderInvocationPlan`
- merge project defaults with that plan
- pass provider-native launch and session inputs into the adapter boundary

It should not:

- invent provider-specific argument syntax
- choose MCP visibility policy

## `vibrant/providers/codex/client.py`

Refocus this file on process launch mechanics.

It should:

- build argv so `app-server` is always present
- append repeated `--config` arguments supplied by the invocation plan
- consume any launch env emitted by the compiler

It should not:

- know how to interpret orchestrator capability policy directly

## `vibrant/providers/codex/adapter.py`

Refocus this file on Codex transport behavior.

It should:

- consume the Codex-relevant part of the invocation plan
- preserve existing thread payload behavior where still appropriate

It should not:

- decide which MCP tools a role may see
- become the authority for binding semantics

## `vibrant/providers/claude/adapter.py`

Refocus this file on Claude SDK behavior.

It should:

- consume only the Claude-relevant projection of the invocation plan
- remain responsible for Claude-native option handling

It should not:

- learn orchestrator binding policy directly
- absorb Codex-oriented MCP concepts

## `vibrant/agents/runtime.py`

This is the correct boundary to carry the invocation plan through both start and
resume operations.

That keeps provider-launch concerns:

- out of prompt construction
- out of orchestrator lifecycle policy
- out of persistent orchestrator stores except for normalized debugging metadata

## `vibrant/orchestrator/gatekeeper/lifecycle.py`

Before launching the Gatekeeper:

- obtain the provider-neutral binding descriptor
- compile it through the active provider compiler
- pass the compiled invocation plan into the runtime

The lifecycle service should not know whether the active provider uses:

- Codex CLI flags
- Claude SDK options
- some future provider-native representation

## `vibrant/orchestrator/execution/coordinator.py`

Use the same mechanism for worker runs once worker MCP policy is ready.

Workers should not get a separate provider-config path. They should use the
same binding, compilation, and runtime handoff model.

## Recommendation

Do not solve this by:

- passing more loose provider kwargs through `AgentBase`
- embedding Codex-specific config generation inside the binding layer
- overloading `extra_config` with process launch semantics

Do solve it by adding:

- one provider-neutral `MCPAccessDescriptor`
- one explicit provider compiler subsystem
- one runtime-facing `ProviderInvocationPlan`

That is the minimum architecture that can support Codex now and other providers
later without scattering provider-specific MCP glue across the orchestrator.

## Sources

- OpenAI Codex Config Basics:
  <https://developers.openai.com/codex/config-basic>
- OpenAI Codex Advanced Config:
  <https://developers.openai.com/codex/config-advanced>
- OpenAI Codex Config Reference:
  <https://developers.openai.com/codex/config-reference>
