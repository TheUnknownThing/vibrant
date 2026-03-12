# Role / Agent / Run Architecture

This document describes the **current implemented architecture** in Vibrant as of **2026-03-12**.

The core model is:

- **Role** = policy and capabilities
- **Agent instance** = stable logical actor identity
- **Run** = one execution of that actor

That split is no longer just a design goal. It is present in the durable model, in the default task-execution path, and in the managed Gatekeeper path.

## Current Status

Vibrant now uses a **real role / agent-instance / run architecture**.

What is already true in the codebase:

- stable agent instances are persisted as `AgentInstanceRecord`
- individual executions are persisted as `AgentRunRecord`
- instance state and run history are stored separately
- orchestrator-facing agent snapshots expose both `agent_id` and `run_id`
- task execution resolves a stable task-scoped instance, then creates a fresh run under it
- the managed Gatekeeper path resolves a stable project-scoped instance, then creates a fresh run per interaction
- provider-thread reuse is anchored to the stable agent identity, not to one long-lived run record

What is still notable:

- some APIs still expose explicit run-history naming alongside instance-aware defaults
- Gatekeeper still has dedicated orchestration logic and a fallback non-managed execution path
- role capability fields exist, but not every behavior is driven from them generically

## Primary Code Locations

The main implementation lives in these files:

- `vibrant/models/agent.py`
  - `AgentInstanceRecord`
  - `AgentRunRecord`
- `vibrant/orchestrator/agents/store.py`
  - `AgentInstanceStore`
  - `AgentRecordStore`
- `vibrant/orchestrator/agents/registry.py`
  - stable-instance resolution
  - run-record creation
  - startup reconciliation from persisted runs
- `vibrant/orchestrator/agents/instance.py`
  - `AgentInstance` protocol
  - `ManagedAgentInstance`
- `vibrant/orchestrator/agents/runtime.py`
  - live runtime-handle management
  - wait / resume / interrupt / kill by stable `agent_id`
- `vibrant/orchestrator/tasks/execution.py`
  - task execution through stable task-scoped agent instances
- `vibrant/orchestrator/gatekeeper_runtime.py`
  - Gatekeeper orchestration through the stable project-scoped instance in the managed path
- `vibrant/orchestrator/facade.py`
  - public read/write facade
- `vibrant/orchestrator/STABLE_API.md`
  - stable public snapshot semantics

## Layered Model

### 1. Role Layer

The role layer is the built-in role catalog in `vibrant/orchestrator/agents/catalog.py`.

`AgentRoleSpec` currently carries:

- `role`
- `display_name`
- `agent_id_prefix`
- `workflow_class`
- `default_provider_kind`
- `default_runtime_mode`
- `supports_interactive_requests`
- `persistent_thread`
- `question_source_role`
- `contributes_control_plane_status`
- `ui_model_name`
- `runtime_builder`

This means the orchestrator can resolve policy from persisted role names instead of branching on closed role enums.

Current built-in roles are:

- `code`
- `merge`
- `test`
- `gatekeeper`

The provider side is similarly catalog-driven through `ProviderKindSpec` and `ProviderKindCatalog`.

### Role Metadata: What Is Authoritative Today

The role catalog is not just descriptive. Some fields already control real behavior today, while others are still mostly documentation or future-policy hooks.

Fields that are already authoritative in the current implementation:

- `agent_id_prefix`
  - used by `AgentRegistry.resolve_instance(...)` to derive stable instance ids
- `default_provider_kind`
  - used by `AgentRegistry.resolve_instance(...)`
  - also used by Gatekeeper bootstrap/runtime setup when a provider is not explicitly overridden
- `default_runtime_mode`
  - used by `AgentRegistry.resolve_instance(...)` to populate instance provider defaults
  - used when Gatekeeper-specific runtime setup falls back to role defaults
- `runtime_builder`
  - used by runtime assembly to construct the concrete runtime for a persisted run
- `question_source_role`
  - used by orchestrator question/state services when attributing structured questions
- `contributes_control_plane_status`
  - used by state/projection logic when determining which roles contribute control-plane status
- `ui_model_name`
  - used by UI-facing projection helpers to map a role to its display/model identity

Fields that exist but are not yet fully authoritative across the system:

- `workflow_class`
  - carried in the catalog, but not yet the main generic dispatch input for orchestration
- `supports_interactive_requests`
  - correctly describes role intent, but is not yet enforced as a generic runtime/facade rule
- `persistent_thread`
  - now drives default managed Gatekeeper thread-resume policy through `ManagedAgentInstance`
  - fallback direct Gatekeeper execution still has bespoke resume logic

So the role layer is already partially authoritative, but it is not yet the sole policy source for every role-dependent branch.

### 2. Agent-Instance Layer

The stable actor layer is represented by `AgentInstanceRecord` plus the in-memory `ManagedAgentInstance` facade.

An agent instance answers questions like:

- who is the logical actor?
- what role does it implement?
- what scope does it belong to?
- what provider defaults belong to this actor?
- what was its latest run?
- what run is active right now?

The instance record contains:

- `identity.agent_id`
- `identity.role`
- `scope.scope_type`
- `scope.scope_id`
- default provider configuration
- `latest_run_id`
- `active_run_id`

Current built-in scope patterns are:

- **task-scoped agents**
  - `agent-task-001`
  - `test-task-001`
  - `merge-task-001`
- **project-scoped Gatekeeper**
  - `gatekeeper-project`

Agent ids are derived from the role's `agent_id_prefix` plus a slugged scope key.

### 3. Run Layer

The execution layer is represented by `AgentRunRecord`.

A run record answers questions like:

- what happened this time?
- which prompt and workspace were used?
- what lifecycle state did this execution reach?
- what summary or error belongs to this execution?
- which provider thread and logs belong to this execution?

Run identity is explicitly separate from stable agent identity:

- stable agent id: `agent-task-001`
- run id: `run-agent-task-001-3f2a9b7c`

That distinction is the substance of the refactor.

## Runtime and Persistence Semantics

The important current runtime rules are:

- stable instances are stored in `.vibrant/agent-instances`
- run records are stored in `.vibrant/agent-runs`
- live runtime handles are tracked by stable `agent_id`
- provider-thread lookup is done per stable `agent_id`, using the latest persisted resumable handle across that agent's runs
- the runtime currently allows **at most one active live run per stable agent instance**

That last point matters: the system is structurally multi-run, but not currently multi-concurrent-run for a single stable agent.

## Current Workflow

This section describes the default behavior that the code implements now.

### Task Execution

When a task runs through `TaskExecutionService`, the flow is:

1. Choose the next task.
2. Create a fresh worktree and build the prompt.
3. Resolve the stable task-scoped `ManagedAgentInstance` for `task.agent_role` and `task.id`.
4. Record a task-run attempt in task state.
5. Create a **new run record** under that stable agent instance.
6. Start the run through `AgentRuntimeService`.
7. Return a `TaskExecutionAttempt` that carries both:
   - the stable `agent` instance
   - the current `agent_record` run record
8. Wait for completion through the stable agent instance.
9. Persist run updates as the runtime progresses.
10. Update the instance record with `latest_run_id` and `active_run_id`.
11. Review, merge, retry, or pause based on the run result.

Important consequence:

> Re-running the same task role creates a new run for the same stable task-scoped actor.

### Gatekeeper Interaction

When Gatekeeper runs through the **managed runtime path**, the flow is:

1. Create a `GatekeeperRequest`.
2. Resolve the stable project-scoped Gatekeeper instance.
3. Optionally reuse the latest persisted provider thread for that stable instance.
4. Create a new run record with:
   - stable `agent_id = gatekeeper-project`
   - fresh `run_id = run-gatekeeper-project-<suffix>`
5. Start the run through `AgentRuntimeService`.
6. Wait for completion through the stable instance when needed.
7. Persist the run into `.vibrant/agent-runs`.
8. Keep the Gatekeeper instance stable across interactions.

Important consequence:

> Gatekeeper continuity is modeled as one stable project actor with many runs.

There is still a fallback path for gatekeeper objects that expose older direct `start_run(...)` or `run(...)` APIs. That fallback is one reason the architecture is still partly transitional.

### Restart and Recovery

On startup, the registry rebuilds instance/run relationships from persisted data:

- load run records from `.vibrant/agent-runs`
- load stable instances from `.vibrant/agent-instances`
- reconcile missing instance records from existing runs
- rebuild derived orchestrator state from the resulting run set

This means recovery is no longer based on one overloaded record pretending to be both durable actor identity and execution history.

## Public API Shape Today

The current public API is intentionally mixed while the refactor settles.

Stable-agent reads:

- `get_agent(agent_id)`
- `get_agent_instance(agent_id)`
- `list_agents(...)`
- `list_agent_instances(...)`

Run-centric reads:

- `get_run(run_id)`
- `list_agent_records()`
- `list_agent_run_records()`
- `records_for_task(task_id)`

Instance-aware default surface at the top level:

- `OrchestratorFacade.snapshot()` returns `OrchestratorSnapshot`
- `OrchestratorSnapshot.agents` is a tuple of stable agent snapshots

So the durable architecture is instance-aware by default, even though some explicit run-history APIs still remain.

## Coordination Boundaries Today

The current implementation is easiest to understand if you separate durable ownership from orchestration ownership.

### Durable ownership

- `AgentRegistry`
  - resolves or creates stable agent instances
  - creates fresh run records beneath a stable instance
  - reconciles instance state from persisted run history on startup
- `AgentInstanceStore`
  - owns stable instance persistence in `.vibrant/agent-instances`
- `AgentRecordStore`
  - owns run persistence in `.vibrant/agent-runs`

### Runtime / orchestration ownership

- `ManagedAgentInstance`
  - is the role-neutral lifecycle facade for one stable actor
  - exposes create/start/resume/wait/interrupt/kill operations in stable-agent terms
- `AgentRuntimeService`
  - owns live handle tracking by stable `agent_id`
  - starts and resumes concrete provider-backed runs
- `TaskExecutionService`
  - owns task-scoped execution flow around worktrees, prompts, retries, review, and merge
  - uses a stable task-scoped `ManagedAgentInstance` as the execution anchor
- `GatekeeperRuntimeService`
  - owns Gatekeeper request orchestration and Gatekeeper-specific fallback handling
  - uses a stable project-scoped `ManagedAgentInstance` in the managed path
- `AgentManagementService`
  - provides the public stable-agent query/control surface consumed by the facade

This is why the architecture is now structurally correct even though some policy still lives in service-specific layers: durable identity and execution history are cleanly separated, but orchestration policy is not yet fully generic.

## Terminology Crosswalk

Some names still reflect the transition from the old run-centric model to the current instance-aware model.

- **Stable instance terminology**
  - `AgentInstanceRecord`
  - `AgentInstanceStore`
  - `AgentInstance`
  - `ManagedAgentInstance`
  - `get_agent_instance(...)`
  - `list_agent_instances(...)`
- **Run terminology still present for explicit history access**
  - `AgentRunRecord`
  - `list_agent_records()`
  - `list_agent_run_records()`
  - `records_for_task(...)`

When reading current APIs, the practical rule is:

> If you need durable history for individual executions, use the run-centric surface.
> If you need the stable logical actor, use the instance-aware surface.

## What This Refactor Already Fixed

The architecture now has these real improvements:

- role identity is string-driven and open to catalog extension
- stable actor identity is distinct from run identity
- task execution can rerun the same stable actor without inventing a new actor id each time
- provider-thread continuity is anchored to the stable actor layer
- agent snapshots can distinguish the actor from one specific execution
- Gatekeeper continuity is modeled as same agent, new run

## Remaining Design Choices

The storage-model split itself is now complete. What remains is mostly about how far to push generic policy and naming cleanup.

### 1. `AgentInstance` is the primary coordination boundary

The codebase now has all of these pieces:

- `AgentInstanceRecord`
- `AgentInstanceStore`
- `AgentInstance` protocol
- `ManagedAgentInstance`

Task execution and the managed Gatekeeper path use this abstraction directly, and the default orchestrator snapshot now exposes instance snapshots by default.

### 2. Some role metadata is authoritative, but not fully generic yet

These fields are still the main places where policy may become more generic over time:

- `workflow_class`
- `supports_interactive_requests`
- `persistent_thread`

Today, `persistent_thread` already influences managed Gatekeeper resume behavior, while the other two fields are still partly descriptive.

### 3. Gatekeeper still carries bespoke orchestration policy

That currently shows up in areas such as:

- busy-state semantics
- fallback execution paths for older gatekeeper interfaces

The model is structurally correct, but not every policy has been pushed behind generic role or instance capabilities yet.

### 4. Some public APIs still expose run-centric terminology

That is now mostly an API-shape choice rather than a storage or migration artifact.

## Recommended Direction

The right next step is **not** another broad naming pass.

The right next step is to keep tightening the remaining policy edges without re-opening the storage model:

- keep moving helper and orchestration paths toward `AgentInstance`
- decide which role fields should become generic policy inputs and which should be removed
- make public read models more consistently instance-aware
- simplify or remove remaining redundant run-centric names when that improves the public API
- continue updating docs so they describe the current instance-aware model first

## Short Checklist

### Implemented

- [x] Separate stable instance records from run records
- [x] Persist instances in `.vibrant/agent-instances`
- [x] Persist runs in `.vibrant/agent-runs`
- [x] Expose both `agent_id` and `run_id` in stable agent snapshots
- [x] Resolve task execution through stable task-scoped instances
- [x] Resolve managed Gatekeeper execution through a stable project-scoped instance
- [x] Add a first-class `AgentInstance` / `ManagedAgentInstance` abstraction
- [x] Keep provider-thread continuity at the stable-agent layer

### Follow-Ups

- [x] Make more helper paths depend directly on `AgentInstance`
- [x] Decide whether role capability fields should become authoritative or be simplified
- [x] Remove the `AgentRunRecord` compatibility alias
- [x] Remove legacy `.vibrant/agents` compatibility reads
- [x] Reshape remaining public read models that still expose run-centric naming by default
- [x] Audit and update older docs that still describe the pre-refactor storage layout

## Final Summary

Vibrant is now best described as:

- **Role** defines policy
- **Agent instance** defines stable actor identity and scope
- **Run** defines one execution of that actor

That architecture is already real in the durable model and in the main orchestration paths.

What remains is mostly API and policy refinement: deciding how much run-centric naming to keep, and how much more Gatekeeper behavior should move behind generic role and instance capabilities.
