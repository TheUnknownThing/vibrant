# Orchestrator Architecture

> Date: 2026-03-15
> Status: current implementation reference and cleanup backlog
> Scope: the live layered orchestrator, its identity model, and the remaining cleanup direction

## Summary

Vibrant's orchestrator should be read along three connected axes:

1. layered ownership
   - `interface -> policy -> basic`
   - `interface -> basic` is allowed for read/query projections only
2. stable actor identity
   - **role** = policy and capability identity
   - **agent instance** = stable logical actor
   - **run** = one execution of that actor
3. orchestrator-owned workflow state
   - the workflow session owns execution status and the task concurrency limit
   - agents and providers consume that policy; they do not define it

The large refactor already landed. The remaining work is cleanup: delete
duplicate paths, keep workflow meaning inside `policy/`, and keep the public
surface aligned with the layered model instead of legacy seams.

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
    __init__.py        # compatibility shim only
```

### Composition root

`vibrant/orchestrator/bootstrap.py` is the composition root. It wires the
system in this order:

1. durable stores and reusable mechanics
2. policy-owned Gatekeeper and task loops
3. interface adapters for first-party consumers
4. the semantic MCP server plus the loopback FastMCP host

`bootstrap.py` should stay a wiring module. It should not become a second place
where workflow or review decisions live.

## Layer Responsibilities

### `basic/`: durable state and reusable mechanics

`basic/` owns the pieces that can be reused across workflows:

- durable stores under `basic/stores/`
- generic agent instance and run persistence
- runtime execution plumbing
- orchestrator-owned conversation persistence and projection
- MCP binding mechanics and transport support
- workspace preparation and merge mechanics

`basic/` may store, resume, bind, and project. It must not decide when a
workflow transition should happen.

### `policy/`: workflow authority

`policy/` owns orchestration meaning:

- `gatekeeper_loop/` owns user-to-Gatekeeper workflow semantics
- `task_loop/` owns task selection, concurrency gating, execution staging, and
  review handling
- `shared/` holds policy helpers reused across loops
- concrete role catalogs, scope rules, prompt shaping, and capability presets
  live here, not in `basic/`

### `interface/`: external adapters

`interface/` exposes first-party read and command surfaces:

- query adapters
- command adapters
- control-plane entry points
- MCP tools, resources, and HTTP transport

It may shape projections and forward commands, but it should not mutate stores
directly or implement workflow semantics on its own.

## Identity Model

The surviving identity model is intentionally small and explicit:

- `session_id`
  - one durable workflow session
- `submission_id`
  - one host-originated Gatekeeper submission
- `task_id`
  - one roadmap task definition
- `attempt_id`
  - one execution attempt for one task
- `role`
  - policy/capability identity
- `agent_id`
  - one stable logical actor instance
- `run_id`
  - one execution of that stable actor
- `conversation_id`
  - one orchestrator-owned conversation stream
- `question_id`
  - one durable user-decision record
- `ticket_id`
  - one durable review ticket
- `event_id`
  - one canonical orchestrator event

Provider-native ids such as `provider_thread_id`, `turn_id`, and `item_id` are
resume or trace handles. They are not orchestrator primary keys.

### Role / instance / run split

The stable actor model is:

- **role** = policy and capability identity
- **agent instance** = stable logical actor identity
- **run** = one execution under that actor

Important consequences:

- stable instances are persisted separately from run records
- one stable actor may accumulate many runs over time
- task ownership belongs to attempts and workflow state, not to generic run
  identity
- conversation routing should resolve producing `run_id` values, not rely on
  stable-agent uniqueness as a hidden invariant

## Workflow Session Ownership

The workflow session is the durable owner of:

- workflow status
- resume status
- Gatekeeper session projection
- total agent spawn count
- `concurrency_limit`

`concurrency_limit` is orchestrator state, not a provider setting and not a TUI
local preference.

Current rule:

- `vibrant.toml` seeds the default limit for a project
- workflow state persists the current limit in `.vibrant/state.json`
- `TaskLoop` enforces the limit by comparing it to active attempts

This means the limit belongs to orchestrator workflow policy even though the
initial value comes from config.

## MCP Transport And Binding Model

The active MCP shape is:

1. a semantic orchestrator MCP server under `interface/mcp/`
2. a loopback FastMCP HTTP host owned by the orchestrator
3. per-run binding registration with server-side filtering
4. provider invocation plans compiled from provider-neutral binding descriptors

Important current rules:

- the active transport is loopback HTTP, not a shared global MCP profile
- bindings are registered per run and enforced server-side
- the HTTP binding identity is carried through `X-Vibrant-Binding`
- provider-specific launch flags are derived from a provider-neutral access
  descriptor, not hard-coded in policy
- root `vibrant/orchestrator/mcp/` is a compatibility import/export shim, not
  the authority layer

## End-To-End Flow Summary

### Planning / Gatekeeper flow

1. the interface receives user input
2. policy decides whether the input is a fresh message or an answer to a
   pending question
3. policy records the host message into the orchestrator-owned conversation
   stream
4. policy resolves the stable project-scoped Gatekeeper instance and creates a
   fresh run
5. runtime events are projected into the orchestrator conversation
6. typed MCP actions update roadmap, consensus, questions, and workflow state
7. policy decides whether planning remains blocked or execution may proceed

### Task execution / review flow

1. `TaskLoop` decides whether execution is currently allowed
2. `TaskLoop` selects an eligible task and checks the available execution slots
3. policy resolves the task-scoped worker instance and creates a fresh run
4. `basic/runtime/` launches the run and `basic/workspace/` manages the
   attempt workspace
5. runtime and conversation events are recorded as orchestrator-owned state
6. policy interprets the typed outcome, creates review tickets when needed, and
   decides accept, retry, escalate, or complete

## Architecture Rules

These rules replace the older redesign notes as the current source of truth.

- The orchestrator owns durable state under `.vibrant/`.
- The orchestrator owns the processed conversation history shown to the TUI.
  Provider logs are observability artifacts, not the product contract.
- The Gatekeeper changes orchestrator state through typed MCP tools, not by
  prose output or file writes.
- `policy/` is the only owner of workflow transitions, question lifecycle,
  concurrency gating, review outcomes, retry behavior, and task progression.
- `basic/` may know how to store, execute, resume, bind, and project. It may
  not decide task readiness, question routing, planning completion, or review
  meaning.
- Concrete role descriptors, scope resolution, thread-reuse policy, prompt
  shaping, and capability presets are policy concerns.
- `interface/`, `facade.py`, `bootstrap.py`, and the TUI must not become second
  authority paths.

## Public And Compatibility Surfaces

These are the consumer-facing orchestrator surfaces that should remain coherent:

- `bootstrap.Orchestrator`
  - composition root and compatibility shell
- `OrchestratorFacade`
  - stable first-party read/write surface
- `InterfaceControlPlane`
  - stable command/query boundary beneath the facade
- `vibrant.orchestrator.mcp`
  - compatibility import path that re-exports the active MCP implementation

The active MCP implementation belongs under `interface/mcp/`. The root
`mcp/__init__.py` package should stay only as a stable import/export shim.

## Cleanup Backlog

The tree is much cleaner than the old flat orchestrator, but the cleanup is not
finished.

### 1. Keep policy as the only workflow authority

These decisions must stay under `policy/` only:

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

### 4. Finish shrinking compatibility residue

Examples:

- root `vibrant/orchestrator/mcp/`
- root module aliases kept for migration convenience
- stale doc references that still describe branch migration or superseded
  temporary designs

### 5. Deepen the execution boundary where needed

The package layout now implies explicit code -> validate -> review -> merge
staging, but validation and merge behavior are still thinner than the intended
final architecture.

## Verification Checklist

Use focused orchestrator checks while cleanup is in flight:

- `uv run pytest tests/test_orchestrator_architecture.py`
- `uv run pytest tests/test_orchestrator_bootstrap.py`
- `uv run pytest tests/test_orchestrator_gatekeeper_loop.py`
- `uv run pytest tests/test_orchestrator_task_loop.py`
- `uv run pytest tests/test_orchestrator_mcp_surface.py`
- `uv run pytest tests/test_orchestrator_mcp_transport.py`
- `uv run pytest tests/test_tui_planning_completion.py`

The docs are in good shape when:

- the layered package map matches the real tree
- each workflow decision has one obvious owner
- `basic/` stays free of concrete workflow policy
- the public surface is documented from the layered model first
- duplicate orchestrator design notes are gone
