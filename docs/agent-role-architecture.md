# Data-Driven Agent Roles and Provider Kinds

This document explains how role-specific workflow logic works today, why it is hard to extend, and what a cleaner future design should look like.

The goal is to make agent **roles** and provider **kinds** data-driven and extensible without losing the small amount of custom logic some roles legitimately need.

## Problem Statement

Today, adding a new role such as `reviewer`, `planner`, or `validator` requires touching multiple layers:

- persisted models
- runtime bootstrapping
- record construction
- workflow services
- state projection
- question ownership
- UI labels and special cases

This happens because role-specific behavior is spread across:

1. enum values
2. agent subclass overrides
3. orchestrator `if/elif` branches
4. hard-coded string checks like `"gatekeeper"`

As a result, the system is only partially extensible.

## Current Architecture: Where Role Logic Lives

### 1. Runtime behavior lives in agent subclasses

Agent subclasses already provide a basic hook model.

- `vibrant/agents/base.py` defines overridable methods such as:
  - `get_agent_type()`
  - `get_thread_runtime_mode()`
  - `get_turn_runtime_mode()`
  - `should_auto_reject_requests()`
  - `get_thread_kwargs()`
  - `enrich_event()`
  - `extract_summary()`
  - `on_run_started()`
  - `on_run_completed()`
- `vibrant/agents/code_agent.py` uses the default execution behavior for code tasks.
- `vibrant/agents/merge_agent.py` overrides runtime modes to require full access.
- `vibrant/agents/gatekeeper.py` overrides interactive request handling and thread persistence behavior.

This part is reasonable: runtime-level differences already have a natural home.

### 2. Workflow behavior lives in orchestrator branching

The bigger problem is that workflow behavior is not owned by a single role abstraction.

Examples:

- `vibrant/orchestrator/bootstrap.py` chooses agent implementations by checking `AgentType`.
- `vibrant/orchestrator/tasks/execution.py` always creates a code agent for task execution.
- `vibrant/orchestrator/gatekeeper_runtime.py` has a dedicated service with custom gatekeeper lifecycle logic.
- `vibrant/orchestrator/state/projection.py` infers gatekeeper status by checking `record.identity.type is AgentType.GATEKEEPER`.
- `vibrant/orchestrator/state/store.py` hard-codes `source_role="gatekeeper"` when translating pending input into user-facing questions.

This means workflow semantics are distributed across the system instead of being described by a single role definition.

### 3. Persisted identity is enum-driven

`vibrant/models/agent.py` currently defines:

- `AgentType.CODE`
- `AgentType.TEST`
- `AgentType.MERGE`
- `AgentType.GATEKEEPER`

That makes role identity closed by default. Every new role requires a code change to the enum before the rest of the system can even talk about it.

### 4. Provider kind is stored as a string, but still effectively hard-coded

`provider.kind` is already a string, which is good, but the actual system still assumes Codex in several places.

So the model looks flexible, but the bootstrap path is not.

## Summary of the Current "Before" Workflow

### Before: creating and running a code agent

1. `TaskExecutionService` decides a task should run.
2. It calls `AgentRegistry.create_code_agent_record(...)`.
3. The record is persisted with `identity.type = AgentType.CODE`.
4. `AgentRuntimeService` asks bootstrap for a runtime.
5. Bootstrap checks `AgentType` and instantiates `CodeAgent`.
6. `CodeAgent` runs with default runtime behavior.

### Before: creating and running a gatekeeper agent

1. Planning or review code decides it needs gatekeeper behavior.
2. It creates a `GatekeeperRequest` with a `GatekeeperTrigger`.
3. `GatekeeperRuntimeService` or `Gatekeeper` builds a gatekeeper-specific `AgentRecord`.
4. The record is persisted with `identity.type = AgentType.GATEKEEPER`.
5. Multiple services special-case gatekeeper behavior:
   - thread resume policy
   - busy state
   - question ownership
   - UI labels
   - role-specific MCP tools

The main issue is that the system does not have one place to answer:

> “What does the `gatekeeper` role mean?”

Instead, the answer is scattered across subclasses, services, state projection, and UI checks.

## Design Goal for the Future

We want a design where:

- a role is identified by a string, not a closed enum
- a provider kind is identified by a string, not an implicit default
- most behavior is declared in metadata/policy
- custom behavior is supplied through narrow hooks or strategy objects
- orchestrator services stop branching on specific role names

The key idea is:

> Use **data-driven role and provider catalogs** for policy, and **small hooks/handlers** for the few places where code is still necessary.

## Proposed Architecture

### A. Role catalog

Introduce a built-in role registry, for example:

```python
@dataclass(slots=True)
class AgentRoleSpec:
    role: str
    display_name: str
    agent_id_prefix: str
    workflow_class: str
    default_provider_kind: str
    default_runtime_mode: str
    supports_interactive_requests: bool
    persistent_thread: bool
    question_source_role: str | None
    contributes_control_plane_status: bool
    ui_model_name: str | None = None
    handler_factory: Callable[..., RoleHandler] | None = None
```

Examples of built-in roles:

- `code`
- `merge`
- `test`
- `gatekeeper`

This spec should describe policy, not implement the whole runtime by itself.

### B. Role handler / strategy object

For behavior that is too rich for flags, each role can optionally provide a handler.

Example interface:

```python
class RoleHandler(Protocol):
    def build_record(self, context: RoleRunContext) -> AgentRecord: ...
    def build_prompt(self, context: RoleRunContext) -> str: ...
    def build_runtime(self, context: RuntimeBuildContext) -> AgentRuntime: ...
    def should_resume_previous_thread(self, context: RoleRunContext) -> bool: ...
    def pending_question_source_role(self, context: RoleRunContext) -> str | None: ...
```

Important constraint:

- handlers should provide **role-local behavior**
- handlers should not directly mutate roadmap or orchestrator state arbitrarily

In other words, hooks are good for “how this role behaves,” not for bypassing service boundaries.

### C. Provider kind registry

Add a provider registry for transport and adapter selection.

```python
@dataclass(slots=True)
class ProviderKindSpec:
    kind: str
    display_name: str
    adapter_factory: Any
    default_transport: str
```
```

Examples:

- `codex`
- future `openai-responses`
- future `local-script`

Then `provider.kind` becomes the lookup key for provider-specific runtime construction.

## Before vs After

### Before: role-specific workflow is scattered

```text
task/review/planning service
  -> decides concrete role through local branching
  -> registry builds role-specific record through helper method
  -> bootstrap switches on AgentType
  -> runtime subclass applies some behavior
  -> state/UI/services perform more role-specific branching later
```

### After: role-specific workflow is catalog-driven

```text
task/review/planning service
  -> asks role catalog for spec by role name
  -> role handler builds prompt/record/runtime behavior
  -> provider catalog resolves adapter by provider.kind
  -> generic services execute using declared capabilities
  -> state/UI read role metadata instead of branching on names
```

## Concrete Comparison

### Before: code path for a task execution role

```python
prompt = prompt_service.build_task_prompt(task, worktree)
record = agent_registry.create_code_agent_record(...)
runtime = build_runtime_from_agent_type(record.identity.type)
```

### After: code path for a task execution role

```python
role_spec = role_registry.get("code")
handler = role_spec.build_handler(...)
prompt = handler.build_prompt(context)
record = handler.build_record(context)
runtime = handler.build_runtime(runtime_context)
```

The orchestrator no longer needs to know what makes `code` special.

### Before: gatekeeper special cases

Today, gatekeeper behavior leaks into:

- run dispatch
- state projection
- question handling
- UI identity
- thread resume policy

### After: gatekeeper expressed as role metadata + handler

`gatekeeper` would be defined declaratively as:

- `supports_interactive_requests = True`
- `persistent_thread = True`
- `contributes_control_plane_status = True`
- `question_source_role = "gatekeeper"`
- `workflow_class = "planning-control"`
- custom handler for prompt assembly and trigger-specific record building

Generic services would use those properties instead of checking `if role == "gatekeeper"`.

## Should We Use Hooks?

Yes, but only in a controlled way.

### Hooks are good for

- prompt construction
- record construction
- deciding resume policy
- runtime construction
- event enrichment
- summary extraction

### Hooks are not good for

- direct roadmap mutation from random role code
- bypassing orchestrator state services
- spreading workflow policy across many unrelated classes

So the future model should be:

- **data for policy and identity**
- **hooks/handlers for behavior**
- **generic services for orchestration**

This is better than either extreme:

- better than “everything is a giant subclass tree”
- better than “everything is static config with no extension points”

## Design Rules

### Rule 1: persisted identity should be open

Replace closed enum-driven role identity with a persisted string field such as:

- `identity.role: str`

Legacy `type` can be migrated on read, but new code should depend on `role`.

### Rule 2: services should depend on capabilities, not names

Bad:

```python
if record.identity.type is AgentType.GATEKEEPER:
    ...
```

Better:

```python
role_spec = role_registry.get(record.identity.role)
if role_spec.contributes_control_plane_status:
    ...
```

### Rule 3: role-local code should be reachable through one abstraction

If a role needs custom logic, the orchestrator should reach it through a standard handler interface, not by importing bespoke services all over the codebase.

### Rule 4: provider lookup should be independent from role lookup

Role decides **what the agent is for**.

Provider kind decides **which backend runs it**.

Those should be related by defaults, not fused together.

## Example Role Specs

### `code`

- workflow class: `execution`
- default runtime mode: `workspace-write`
- interactive requests: no
- persistent thread: no
- question source role: none

### `merge`

- workflow class: `merge`
- default runtime mode: `danger-full-access`
- interactive requests: no
- persistent thread: no

### `test`

- workflow class: `validation`
- default runtime mode: `read-only`
- interactive requests: no
- persistent thread: no

### `gatekeeper`

- workflow class: `planning-control`
- default runtime mode: `read-only`
- interactive requests: yes
- persistent thread: yes
- question source role: `gatekeeper`
- contributes control-plane status: yes

## Migration Plan

### Phase 1: introduce catalogs without changing behavior

- add `AgentRoleSpec` and `ProviderKindSpec`
- register current built-in roles and the `codex` provider
- keep existing enums temporarily as compatibility inputs

### Phase 2: migrate persisted identity

- add `identity.role`
- migrate old `type` values when loading records
- update filters and snapshots to read `role`

### Phase 3: move bootstrap/runtime selection to registries

- replace `AgentType` branching in orchestrator bootstrap
- resolve role handler and provider adapter through registries

### Phase 4: eliminate scattered role checks

- replace gatekeeper-specific checks in projection/state/question code with role capabilities
- move UI identity labels to role metadata

### Phase 5: remove obsolete enum- and branch-based paths

- delete compatibility-only factories and branches
- keep the runtime model centered on role strings + registry lookup

## Expected Benefits

- adding a new role becomes a registry entry plus a handler, not a cross-cutting refactor
- provider kinds become truly extensible
- workflow semantics become easier to understand and test
- state and UI logic can read declared capabilities instead of relying on special names
- the gatekeeper remains special where needed, but special in one place rather than everywhere

## Recommended Direction

The future design should use:

- **role specs** for identity and policy
- **provider specs** for backend wiring
- **role handlers/hooks** for small, focused custom behavior
- **generic orchestrator services** that consume those abstractions

In short:

> Make roles and provider kinds data-driven first, and use hooks only where real behavior differences still need code.


## Role vs Agent vs Run

This distinction should be made explicit in the future design.

### Role

A **role** describes behavior and policy.

Examples:

- `gatekeeper`
- `code`
- `merge`
- `test`

A role answers questions like:

- what is this actor for?
- what runtime mode does it default to?
- can it accept interactive requests?
- should it reuse provider threads?
- how should prompts and records be built?

A role is **not** the durable actor itself.

### Agent

An **agent** is a first-class logical actor.

Examples:

- the project's gatekeeper agent
- the code agent assigned to task `task-001`
- the merge agent for task `task-001`

An agent answers questions like:

- which logical actor is this?
- what role does it implement?
- what project/task scope does it belong to?
- what interactions does it expose?
- what is its latest run?

An agent should be a stable identity across multiple invocations.

### Run

A **run** is one execution of an agent.

A run answers questions like:

- what happened this time?
- what prompt was used?
- did it complete, fail, or await input?
- which provider thread did it use or resume?
- what logs and summary belong to this execution?

### Provider Thread

A **provider thread** is conversation continuity at the backend/provider layer.

It is not the same as the agent identity and not the same as the run identity.

A single agent may have many runs, and multiple runs may attach to the same provider thread.

## Clarifying the Current Gatekeeper Behavior

Today, gatekeeper behavior is effectively:

- create a **new `AgentRecord`** for each gatekeeper invocation
- optionally **reuse the latest provider thread** for continuity

So the current model is not:

- “resume the same agent record”

It is closer to:

- “start a new run, maybe on the same underlying provider conversation thread”

That is why the current model feels ambiguous: `AgentRecord` is acting like a run record, while other code sometimes treats it as the durable agent identity.

## Future Model: Agent as a First-Class Actor

The future design should treat an agent as a first-class actor/service boundary.

In that design:

- the **role** provides behavior and policy
- the **agent instance** provides identity and interactions
- the **run record** captures one execution
- the **runtime service** executes the run
- the **storage services/repositories** persist agent and run state

A useful mental model is:

> Role defines how an agent behaves. Agent defines who the actor is. Run defines what happened this time.

## Proposed Agent Instance Interface

An agent instance should expose a stable, role-neutral interaction surface.

```python
class AgentInstance(Protocol):
    agent_id: str
    role: str

    async def start_run(self, request: AgentRunRequest) -> AgentRunHandle: ...
    async def send_input(self, request: AgentInputRequest) -> AgentRunHandle: ...
    async def interrupt_run(self, run_id: str) -> None: ...
    def active_run(self) -> AgentRunRecord | None: ...
    def latest_run(self) -> AgentRunRecord | None: ...
    def list_runs(self) -> list[AgentRunRecord]: ...
    def snapshot(self) -> AgentSnapshot: ...
```

Role-specific implementations should plug into that surface through role handlers, not through ad hoc orchestrator branches.

## Where Durable Storage Should Live

### Short answer

Yes, durable storage should still be managed by agent-related services, but **not hidden inside role implementations and not conflated with the agent instance itself**.

That is, storage ownership should remain in the agent subsystem, but with clearer boundaries.

### Recommended split

The future architecture should separate these responsibilities:

- **Agent repository / store**
  - persists durable agent identities
  - loads and lists agent instances
- **Agent run repository / store**
  - persists run records
  - tracks latest run, active run, run history
- **Agent runtime service**
  - executes one run against a provider backend
  - returns handles and normalized run results
- **Agent directory / manager service**
  - creates agent instances
  - resolves agents by id, task scope, or role
  - coordinates agent instance + repositories + runtime
- **Role registry**
  - resolves role policy and handlers
- **Provider registry**
  - resolves provider adapter/runtime wiring

### What the agent instance should own

An agent instance should own lifecycle semantics such as:

- which runs belong to it
- whether a new request should start a fresh run
- whether a previous provider thread should be reused
- what interactions are valid for this role

### What the agent instance should not own directly

An agent instance should not directly be responsible for:

- writing JSON files by itself
- choosing file paths ad hoc
- rebuilding global orchestrator state
- mutating roadmap or consensus directly

Those should remain responsibilities of dedicated services.

## Relation Between Agent Instances and Agent Services

The relation should be:

- services **manage durable state and cross-agent coordination**
- agent instances **encapsulate one actor's lifecycle and interface**

In other words:

- agent services are the system-level management layer
- agent instances are the actor-level domain objects

A good practical pattern is:

1. a higher-level orchestrator service asks the agent manager for an agent instance
2. the manager resolves the role spec and loads or creates the agent instance
3. the agent instance asks the runtime service to execute a run
4. repositories persist agent metadata and run metadata
5. the orchestrator consumes snapshots/results without needing role-specific branches

## Before vs After for Gatekeeper

### Before

```text
planning service
  -> build GatekeeperRequest
  -> build a fresh AgentRecord
  -> maybe reuse old provider thread id
  -> persist record as if it were the agent identity
```

### After

```text
planning service
  -> resolve the stable gatekeeper agent instance
  -> ask it to start a new run
  -> agent decides whether to reuse prior provider thread
  -> run store persists a new run under the same agent
```

This is much clearer because:

- the gatekeeper remains one stable actor
- each interaction becomes a separate run
- provider thread reuse is a policy of the agent/role, not an identity hack

## Recommended Data Model Direction

A future storage model should look more like:

```python
@dataclass(slots=True)
class AgentInstanceRecord:
    agent_id: str
    role: str
    scope_type: str
    scope_id: str | None
    provider_kind: str
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class AgentRunRecord:
    run_id: str
    agent_id: str
    status: str
    prompt_used: str | None
    started_at: datetime | None
    finished_at: datetime | None
    summary: str | None
    error: str | None
    provider_thread_id: str | None
    provider_thread_path: str | None
```

This keeps:

- identity stable at the agent level
- execution history stable at the run level
- provider continuity explicit but separate

## Final Recommendation on Storage Ownership

So the answer is:

- **Yes**, storage should remain in agent-related services/repositories.
- **No**, role implementations should not manage durable storage directly.
- **No**, one run record should not continue to pretend to be the durable agent identity.
- **Yes**, agent instances should be first-class actors that are coordinated by these services.

A concise rule is:

> Agent instances own behavior and lifecycle semantics. Agent services own persistence and coordination.
