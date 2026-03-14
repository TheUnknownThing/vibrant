# Architecture Layer Refactor

> Date: 2026-03-14
> Status: current architecture reference and cleanup plan
> Scope: the implemented orchestrator shape plus the remaining cleanup direction

## Summary

Vibrant's orchestrator now has two architectural axes that need to be read
together:

1. a layered package split
   - `interface -> policy -> basic`
   - `interface -> basic` is allowed for read/query projections
2. a stable agent model
   - **role** = policy and capabilities
   - **agent instance** = stable logical actor identity
   - **run** = one execution of that actor

The first big refactor is already visible in the source tree. The remaining work
is mostly cleanup: deleting duplicate paths, tightening ownership boundaries, and
keeping the public surface aligned with the layered model instead of preserving
old internal seams.

## Current Implemented Shape

### Package map

```text
vibrant/orchestrator/
  bootstrap.py
  facade.py
  STABLE_API.md
  types.py

  basic/
    artifacts/
    binding/
    conversation/
    events/
    runtime/
    stores/
    workspace/

  policy/
    contracts.py
    shared/
    gatekeeper_loop/
    task_loop/

  interface/
    backend.py
    basic.py
    control_plane.py
    policy.py
    mcp/

  mcp/
    __init__.py        # compatibility shim
```

### Composition root

`vibrant/orchestrator/bootstrap.py` is the composition root. It wires the
system in this order:

1. durable stores and capability bundles
2. reusable runtime, conversation, binding, and workspace mechanics
3. policy-owned Gatekeeper and task loops
4. interface adapters for backend, control-plane, facade, and MCP consumers

`bootstrap.py` should stay a wiring module. It should not become a second place
where workflow decisions live.

### Layer responsibilities

#### `basic/`: durable state and reusable mechanics

`basic/` owns the pieces that can be reused across multiple workflows:

- durable stores under `basic/stores/`
- generic instance/run persistence for agents
- runtime execution plumbing
- orchestrator-owned conversation persistence and projection
- MCP binding mechanics
- workspace preparation and review/merge mechanics

`basic/` may persist data and perform work, but it must not decide when a
workflow transition should happen.

#### `policy/`: workflow authority

`policy/` owns the actual orchestration rules:

- `gatekeeper_loop/` owns user <-> Gatekeeper workflow semantics
- `task_loop/` owns task selection, execution staging, review handling, and
  completion rules
- `shared/` holds policy helpers that are reused across more than one loop
- concrete role catalogs, scope rules, capability presets, and prompt shaping
  live here, not in `basic/`

#### `interface/`: external adapters

`interface/` exposes the orchestrator to first-party consumers:

- read/query adapters
- command adapters
- unified control-plane entry points
- MCP tools/resources/transport

It may shape projections and forward commands, but it should not mutate stores
directly or implement workflow semantics on its own.

## End-to-End Flow Summary

### Planning / Gatekeeper flow

1. the interface receives user input
2. policy decides whether the input is a fresh message or an answer to a
   pending question
3. policy records the host-side message into the orchestrator-owned
   conversation stream
4. policy asks the Gatekeeper lifecycle to resume or start the stable
   Gatekeeper instance and create a fresh run
5. runtime events are projected back into the orchestrator conversation
6. typed MCP actions update roadmap, consensus, questions, and workflow state
7. policy decides whether planning remains blocked or execution may proceed

### Task execution / review flow

1. `TaskLoop` decides whether execution is currently allowed
2. `TaskLoop` selects an eligible task and creates an attempt
3. policy resolves the task-scoped worker instance and creates a fresh run
4. `basic/runtime/` launches the run and `basic/workspace/` manages the
   attempt workspace
5. runtime and conversation events are recorded as orchestrator-owned state
6. policy interprets the typed outcome, creates review tickets when needed, and
   decides accept / retry / escalate / complete

## Architecture Rules

These rules replace the older redesign notes as the current source of truth.

- The orchestrator owns durable state under `.vibrant/`.
- The orchestrator owns the processed conversation history shown to the TUI.
  Provider logs are observability artifacts, not the product contract.
- The Gatekeeper changes orchestrator state through typed MCP tools, not by
  prose output or file writes.
- `policy/` is the only owner of workflow transitions, question lifecycle,
  review outcomes, retry/escalate behavior, and task progression.
- `basic/` may know how to store, execute, resume, bind, and project. It may
  not decide task readiness, question routing, planning completion, or review
  meaning.
- Concrete role descriptors, scope resolution, thread-reuse policy, prompt
  shaping, and capability presets are policy concerns.
- `interface/`, `facade.py`, `bootstrap.py`, and the TUI must not become second
  authority paths.

## Identity and Ownership Model

The surviving identifier model is intentionally small:

- `session_id`
  - one durable workflow session
- `submission_id`
  - one host-originated Gatekeeper submission
- `task_id`
  - one roadmap task definition
- `attempt_id`
  - one execution attempt for one task
- `agent_id`
  - one stable logical actor instance
- `run_id`
  - one execution of that stable actor
- `conversation_id`
  - one durable orchestrator conversation stream
- `question_id`
  - one durable user-decision record
- `ticket_id`
  - one durable review ticket
- `event_id`
  - one canonical orchestrator event

Provider-native ids such as `provider_thread_id`, `turn_id`, and `item_id` are
trace or resume handles. They are not orchestrator primary keys.

## Public and Compatibility Surfaces

There are only three consumer-facing surfaces that should stay coherent:

- `bootstrap.Orchestrator`
  - composition root and compatibility shell
- `OrchestratorFacade`
  - stable first-party read/write surface
- `vibrant.orchestrator.mcp`
  - compatibility import path that re-exports the active MCP implementation

The active MCP implementation belongs under `interface/mcp/`. The root
`mcp/__init__.py` package should stay only as a stable import/export shim.

## Current Gaps

The tree is much cleaner than the old flat orchestrator, but the cleanup is not
finished.

### 1. Some mechanics still have wrapper residue

`basic/` now exists as the main package, but some capability entry points still
mirror or delegate to older root-level modules. That keeps the dependency
direction visually ambiguous.

### 2. Workflow authority still leaks in a few places

Examples that still need pressure:

- TUI behavior that infers planning completion or input routing instead of
  consuming policy snapshots
- facade helpers that perform convenience selection logic instead of delegating
  to policy-owned question/task helpers
- interface adapters that still know too much about direct store mutation

### 3. The execution boundary is still shallower than the package split

The package structure now implies explicit code -> validate -> review -> merge
staging, but validation and merge behavior are still thinner than the final
architecture intends.

### 4. Compatibility shims still need to shrink

Examples:

- root `vibrant/orchestrator/mcp/`
- root module aliases kept for migration convenience
- stale doc references that still describe the older flat layout

## Cleanup Direction

### 1. Keep policy as the only workflow authority

Move or keep these decisions under `policy/` only:

- pending-question selection and answer routing
- planning-complete interpretation
- workflow `PLANNING -> EXECUTING` transitions
- task readiness and concurrency gating
- review outcome mapping
- workflow completion and blocking rules

### 2. Keep `basic/` reusable and role-neutral

`basic/` may own:

- generic instance/run stores
- runtime bookkeeping
- conversation plumbing
- binding mechanics
- workspace and artifact mechanics

It should not regain:

- Gatekeeper-specific lifecycle policy
- task-loop-specific runner policy
- concrete role catalogs
- question or review semantics

### 3. Keep interface and facade thin

- interface adapters should forward commands and expose read models
- facade helpers should be namespace and projection helpers, not workflow
  engines
- the TUI should dispatch actions and render policy/basic projections rather
  than infer behavior from raw runtime events

### 4. Delete duplicate paths instead of preserving them

When a caller has moved to the new layered location, delete the old path in the
same cleanup pass. The goal is one clear ownership path per capability.

## Practical Mental Model

Use these questions to decide where code or docs belong:

- "Can another workflow reuse this without inheriting Gatekeeper or task-loop
  semantics?"
  - if yes, it probably belongs in `basic/`
- "Does this decide what the workflow means or what should happen next?"
  - if yes, it belongs in `policy/`
- "Does this mostly project state or forward commands to the real owner?"
  - if yes, it belongs in `interface/`

Then apply the stable agent model inside that layer:

- role = policy and capabilities
- agent instance = stable logical actor
- run = one execution under that actor

## Verification Checklist

Use focused orchestrator checks while cleanup is in flight:

- `uv run pytest tests/test_orchestrator_architecture.py`
- `uv run pytest tests/test_orchestrator_bootstrap.py`
- `uv run pytest tests/test_orchestrator_gatekeeper_loop.py`
- `uv run pytest tests/test_orchestrator_task_loop.py`
- `uv run pytest tests/test_orchestrator_mcp_surface.py`
- `uv run pytest tests/test_tui_planning_completion.py`

The docs are in good shape when:

- the layered package map matches the real tree
- each workflow decision has one obvious owner
- `basic/` stays free of concrete workflow policy
- the public surface is documented from the layered model first
- duplicate orchestrator design notes are gone
