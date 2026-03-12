# Role / Agent / Run Architecture

This document describes the **current** role / agent / run architecture in Vibrant, what was actually implemented, how the workflow behaves now, and what still remains to be done.

The key architectural point is still the same:

- **Role** = policy and behavior
- **Agent instance** = stable logical actor identity
- **Run record** = one execution of that actor

The important correction is that this is **no longer only a future direction**. The codebase now has the core persistence and orchestration split in place. What remains is mostly about making that split more explicit and more consistently authoritative across the rest of the system.

## Status

As of **2026-03-12**, the architecture is in a **hybrid but structurally real role / agent / run model**.

### What is now implemented

The core structural refactor is now present in the codebase:

- persisted **stable agent instances** exist as `AgentInstanceRecord`
- persisted **run records** exist as `AgentRunRecord`
- stable agents and run history are stored separately
- orchestrator snapshots now expose both **`agent_id`** and **`run_id`**
- task execution resolves a **stable task-scoped agent instance** and then creates a new run under it
- Gatekeeper now uses a **stable project-scoped agent instance** and creates one run per interaction
- provider thread reuse is now tied to the stable agent identity instead of pretending one run record is the durable actor

### What is still transitional

The implementation is not fully finished in every layer.

The main remaining gaps are:

- there is now a first-class in-memory `AgentInstance` / `ManagedAgentInstance` abstraction, but not every orchestration path depends on it yet
- `AgentRecord` still exists as a compatibility alias for the run record model
- some role semantics are still implemented through dedicated services instead of a generic role/instance policy surface
- some compatibility reading of legacy `.vibrant/agents` data is still present for migration/recovery

So the system is **no longer run-centric in storage**, but it is still **partly transitional in API shape and service boundaries**.

## Core Architectural Point

The central idea of this design remains:

> The real refactor is not just “make roles data-driven.”
> The real refactor is to separate **Role**, **Agent Instance**, and **Run** into explicit layers.

That split is now present in the codebase in the durable model and in the orchestration path:

- role metadata defines policy defaults
- stable agent instances define identity and scope
- run records capture execution history

Data-driven catalogs are still important, but they are a **supporting mechanism inside the role layer**, not the whole architecture.

## Current Model in the Codebase

### 1. Role layer

The role layer is represented by the built-in role catalog.

It currently provides:

- role name
- display name
- agent id prefix
- workflow class metadata
- default provider kind
- default runtime mode
- interactive-request capability metadata
- persistent-thread capability metadata
- question-source metadata
- control-plane contribution metadata
- role-specific runtime construction

In practice, this means the orchestrator can resolve policy from the role name instead of branching on closed enums.

### 2. Agent-instance layer

The stable actor layer is now represented by persisted agent instance records.

An agent instance now answers questions like:

- who is the logical actor?
- what role does it implement?
- what scope does it belong to?
- what provider defaults belong to this actor?
- what is its latest run?
- what run is currently active?

Current built-in scope patterns are:

- **task-scoped execution actors**
  - example: `agent-task-001`
  - example: `test-task-001`
  - example: `merge-task-001`
- **project-scoped gatekeeper actor**
  - `gatekeeper-project`

### 3. Run layer

The run layer is now represented by explicit run records.

A run record answers questions like:

- what happened this time?
- what prompt and workspace were used?
- what lifecycle state did this run reach?
- what summary/error belongs to this execution?
- which provider thread/logs belong to this execution?

A run now has its own stable `run_id`, separate from the stable `agent_id`.

Example shape:

- stable agent id: `agent-task-001`
- run id: `run-agent-task-001-3f2a9b7c`

That distinction is the core of the refactor.

## Where Role Logic Lives Now

### Runtime behavior

Runtime-level differences still naturally live close to agent/runtime construction.

Examples:

- code role uses the standard execution runtime
- merge role uses a more privileged runtime mode
- gatekeeper role supports interactive requests and persistent conversation continuity

This part is fine: runtime-level variation does belong near the runtime layer.

### Workflow behavior

Workflow behavior is now split across:

- role metadata in the catalog
- agent-instance resolution in the registry
- run creation in the registry
- execution in the runtime service
- orchestration in task and gatekeeper services
- state projection / question handling in generic services

This is better than the old enum-driven model, but not fully unified yet. Some workflow behavior is still expressed through dedicated services, especially around Gatekeeper.

### Persistence and recovery

Persistence is now split into two durable stores:

- **agent instance store**
  - stable identities in `.vibrant/agent-instances`
- **agent run store**
  - run history in `.vibrant/agent-runs`

For migration and restart recovery, legacy `.vibrant/agents` data is still read when present.

## Current Workflow

This section describes how the system actually behaves **now**, not the historical design.

### Current workflow: task-scoped execution

When a task is executed now, the workflow is:

1. `TaskExecutionService` chooses a task to run.
2. It builds the task prompt.
3. It asks `AgentRegistry.create_execution_agent_record(...)` for a run record.
4. The registry resolves the task's role from `task.agent_role`.
5. The registry resolves or creates the stable task-scoped agent instance.
   - for example `agent-task-001`
   - or `test-task-001`
6. The registry creates a **new run record** under that stable agent.
   - for example `run-agent-task-001-<suffix>`
7. The runtime service starts that run.
8. Live runtime handles are tracked by the **stable agent id**.
9. When the run finishes, the run record is updated and the instance record is updated with:
   - `latest_run_id`
   - `active_run_id`
10. Orchestrator-facing snapshots expose both:
   - `agent_id` = stable actor identity
   - `run_id` = current/latest execution

Important consequence:

> Re-running the same task role creates a **new run** for the **same stable task actor**, instead of inventing a brand-new actor identity each time.

### Current workflow: Gatekeeper interaction

Gatekeeper is now structurally different from the old model.

When Gatekeeper runs now, the workflow is:

1. Planning / review / question-answering code creates a `GatekeeperRequest`.
2. Gatekeeper builds a run record with:
   - stable `agent_id = gatekeeper-project`
   - fresh `run_id = run-gatekeeper-project-<suffix>`
3. If resume behavior is enabled, Gatekeeper looks for the latest persisted provider thread across prior gatekeeper runs.
4. The runtime service starts a **new run** for the stable gatekeeper actor.
5. The run persists into the run store.
6. The gatekeeper instance remains the same stable actor across interactions.

Important consequence:

> Gatekeeper is now modeled as **one stable project actor with many runs**, which is exactly the intended meaning of the agent layer.

### Current workflow: restart / recovery

On restart, the system now rebuilds state from persisted run history and stable instances.

In practice:

- run records are loaded from `.vibrant/agent-runs`
- legacy run data can still be loaded from `.vibrant/agents`
- instance records are loaded from `.vibrant/agent-instances`
- if legacy run data exists without a matching instance record, instances are reconciled from runs
- derived orchestrator state is rebuilt from persisted run data

This means the runtime no longer depends on one overloaded record shape pretending to be both identity and execution history.

## What This Fixes

The main architectural improvements now in place are:

- role identity is open and string-driven
- stable actor identity is distinct from run identity
- Gatekeeper continuity is modeled as **same agent, new run**, not **same run pretending to continue forever**
- task roles can be selected by policy without forcing the whole system back into hard-coded role enums
- provider thread reuse is anchored to the stable actor layer
- snapshots can distinguish the actor from a specific execution

This is the real substance of the refactor.

## What Is Still Not Fully Finished

Even though the core storage/orchestration split is implemented, there are still meaningful follow-up tasks.

### 1. A first-class `AgentInstance` abstraction now exists

The codebase now has all of the following layers in real code:

- `AgentInstanceRecord`
- `AgentInstanceStore`
- `AgentInstance` protocol
- `ManagedAgentInstance` runtime/domain facade

That instance abstraction now owns the stable-agent lifecycle surface directly, including operations such as:

```python
class AgentInstance(Protocol):
    async def start_run(...): ...
    async def resume_run(...): ...
    def latest_run(...): ...
    def active_run(...): ...
    def snapshot(...): ...
```

Task execution and managed Gatekeeper execution now both resolve a stable agent instance first and then create/start a new run through that instance.

What is still incomplete is not the existence of the abstraction itself, but its adoption as the only coordination boundary everywhere in the codebase.

### 2. Some role fields are still descriptive more than authoritative

The following role fields exist, but are not yet used consistently as the one generic source of truth:

- `workflow_class`
- `supports_interactive_requests`
- `persistent_thread`

Today they inform behavior, but not every behavior is driven from them generically.

### 3. Gatekeeper still has dedicated orchestration code

This is not necessarily wrong, but it is still a sign that the generic role/instance model is not yet the only path.

Examples that still need refinement:

- dedicated gatekeeper runtime coordination
- gatekeeper-specific busy-state semantics
- gatekeeper-specific resume-selection logic

The model is structurally correct now, but some of the policy is still implemented in bespoke services.

### 4. Compatibility cleanup is not complete

The refactor intentionally preserves some migration bridges.

Examples:

- `AgentRecord` still aliases the run record model
- legacy `.vibrant/agents` data is still read
- some older docs and names still refer to the older run-centric vocabulary

Those should eventually be removed once the new model is fully normalized across the project.

## Recommended Direction From Here

The right direction now is **not** another round of string replacement or “more data-driven role names.”

The right direction is to finish making the already-implemented split fully authoritative:

- continue moving more orchestration paths to depend on the explicit agent-instance abstraction directly
- move more policy behind generic role/instance capabilities
- remove remaining compatibility aliases and legacy paths
- keep role metadata focused on policy, not on replacing every service boundary

## TODO List

The checklist below reflects the **current post-refactor state**.

### Completed core structural work

These were the core of the refactor, and they are now in place.

- [x] Introduce a first-class `AgentInstanceRecord` separate from the run record model
- [x] Introduce an explicit run record model with a distinct `run_id`
- [x] Introduce a dedicated run repository/store separate from the instance repository/store
- [x] Add stable actor identity for long-lived agents such as the project gatekeeper
- [x] Define stable scopes for built-in actors, including project-scoped gatekeeper and task-scoped execution roles
- [x] Update orchestrator-facing snapshots so callers can distinguish `agent_id` from `run_id`
- [x] Make `latest_for_task(...)` operate on explicit run history rather than implicit single-record identity

### Completed supporting role-system work

- [x] Replace closed role enums with persisted `identity.role`
- [x] Introduce `AgentRoleSpec` / `AgentRoleCatalog`
- [x] Introduce `ProviderKindSpec` / `ProviderKindCatalog`
- [x] Resolve runtime construction through the role catalog
- [x] Use role metadata for default provider kind and runtime mode
- [x] Use role capability metadata for control-plane status projection
- [x] Use role capability metadata for question-source projection where available
- [x] Make task execution choose role from workflow/policy instead of always creating `code` runs

- [x] Replace remaining hard-coded `"gatekeeper"` defaults in question/state/facade APIs
- [x] Move UI-facing role naming/model identity behind role metadata consistently

### Remaining structural work

These items are still important, but they are no longer the main storage-model refactor.

- [x] Introduce a first-class `AgentInstance` runtime/domain abstraction with role-neutral lifecycle operations
- [ ] Continue shifting orchestration call sites to depend on `AgentInstance` directly where that improves clarity
- [ ] Decide whether agent instances themselves should own more lifecycle policy directly, or whether registry/manager services should remain the main coordination boundary
- [ ] Decide whether multiple concurrent runs per stable agent should ever be supported, and if so how active-run semantics should be expressed

### Remaining supporting role-system work

- [ ] Move Gatekeeper thread-resume policy behind a generic role-level or instance-level policy surface
- [ ] Move Gatekeeper busy-state semantics behind generic role/instance capabilities
- [ ] Decide whether `workflow_class` should become an authoritative dispatch input or be removed
- [ ] Decide whether `supports_interactive_requests` should be enforced generically by runtime/bootstrap layers
- [ ] Decide whether `persistent_thread` should drive generic resume behavior or be replaced by a richer hook/policy abstraction
- [ ] Broaden provider extensibility beyond the currently exercised built-in `codex` provider kind

### Remaining cleanup work

- [ ] Remove the `AgentRecord` compatibility alias once the codebase consistently uses explicit run-record naming everywhere
- [ ] Remove legacy `.vibrant/agents` compatibility reads once migration support is no longer needed
- [ ] Remove historical/stale references to `AgentType` and `identity.type` from older docs and comments
- [ ] Update any remaining docs that still describe the architecture as primarily run-centric or merely “data-driven”
- [ ] Add focused tests for the explicit agent-instance abstraction if and when that layer is introduced

## Final Summary

The architecture is now best described as:

- **Role** defines policy
- **Agent instance** defines stable actor identity and scope
- **Run** defines one execution of that actor

That core split is now implemented in the durable model and in the runtime workflow.

What remains is not the original “major refactor.”
What remains is the work of making the new split cleaner, more generic, and more explicit across every API and service boundary.
