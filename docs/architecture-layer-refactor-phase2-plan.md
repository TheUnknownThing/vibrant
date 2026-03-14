# Architecture Layer Refactor Phase 2 Plan

> Date: 2026-03-14
> Status: active cleanup plan
> Scope: finish the layered rewrite without undoing the current role / instance / run model

## Goal

Phase 1 made the layered architecture real. Phase 2 should make it clean.

The end state is:

1. `policy/` is the only owner of workflow behavior
2. `basic/` is the source of truth for reusable mechanics and durable plumbing
3. `interface/` is a thin adapter over policy commands and basic read models
4. the role / instance / run model stays generic and is not pushed back into
   workflow-specific code
5. duplicate root paths and compatibility shims are deleted instead of kept

## Current Constraints

This plan has to match the tree that exists today, not the earlier draft state.

Important facts:

- generic instance/run persistence already lives under:
  - `vibrant/orchestrator/basic/stores/agent_instances.py`
  - `vibrant/orchestrator/basic/stores/agent_runs.py`
- workflow-specific runners already live under:
  - `vibrant/orchestrator/policy/gatekeeper_loop/`
  - `vibrant/orchestrator/policy/task_loop/`
- concrete role and capability policy already lives in:
  - `vibrant/orchestrator/policy/gatekeeper_loop/roles.py`
  - `vibrant/orchestrator/policy/task_loop/roles.py`
  - `vibrant/orchestrator/policy/shared/capabilities.py`
- the active MCP implementation already lives under:
  - `vibrant/orchestrator/interface/mcp/`
- root `vibrant/orchestrator/mcp/` still exists mainly as a compatibility layer
- the focused architecture tests already exist and should be treated as the
  safety net:
  - `tests/test_orchestrator_architecture.py`
  - `tests/test_orchestrator_bootstrap.py`
  - `tests/test_orchestrator_gatekeeper_loop.py`
  - `tests/test_orchestrator_task_loop.py`
  - `tests/test_orchestrator_mcp_surface.py`
  - `tests/test_tui_planning_completion.py`

## Boundary Rule

Use this rule for every move:

- if code can be reused to build both current loops, or another future
  workflow, it may live in `basic/`
- if code decides roles, scopes, prompts, thread reuse, question semantics,
  review semantics, workflow transitions, or task progression, it must live in
  `policy/`
- if code mainly projects read models or forwards commands, it belongs in
  `interface/`

Concretely:

- `basic/` may own generic instance/run stores, runtime bookkeeping,
  conversation plumbing, binding mechanics, and workspace mechanics
- `basic/` must not own Gatekeeper-specific session policy, task-loop launch
  policy, concrete role catalogs, or review/question semantics
- `policy/` must own concrete role descriptors, scope resolution, prompt
  shaping, capability presets, and workflow meaning
- `interface/` may expose a convenient surface, but it must not become a second
  authority path

## Surviving Target Shape

```text
vibrant/orchestrator/
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
      capabilities.py
      workflow.py
    gatekeeper_loop/
      lifecycle.py
      loop.py
      models.py
      questions.py
      requests.py
      roles.py
      state.py
      transitions.py
    task_loop/
      execution.py
      loop.py
      models.py
      projections.py
      prompting.py
      reviews.py
      roles.py
      selection.py
      state.py

  interface/
    backend.py
    basic.py
    control_plane.py
    policy.py
    mcp/
      server.py
      tools.py
      resources.py
      transport.py

  facade.py
  bootstrap.py
  mcp/__init__.py      # import/export shim only
```

## Current Gaps

### 1. `basic/` still has some wrapper residue

The main package layout is correct, but some capability entry points still
delegate into root modules or keep compatibility attributes alive longer than
necessary.

### 2. Workflow authority still leaks outside `policy/`

The main leak points are still:

- TUI logic that infers behavior from raw events instead of policy projections
- interface code that still knows too much about store mutation
- facade helpers that select questions or task behavior instead of delegating to
  policy-owned helpers

### 3. The mechanics boundary is still blurry in task execution

`TaskLoop` owns the workflow state machine, but some validation, diff, and
review-preparation mechanics still need a cleaner handoff between reusable
mechanics and policy-owned stage transitions.

### 4. Compatibility residue remains

Examples:

- root `vibrant/orchestrator/mcp/`
- root module aliases used for migration convenience
- docs that still describe the superseded orchestrator-only proposal set

### 5. Public projections are still partly transitional

The stable surface is much better than the old flat API, but some reads still
mix stable instance identity and run-specific details more than necessary.

## Workstream 1: Lock Ownership With Tests

Objective: keep the intended authority model enforced while cleanup continues.

Focus tests:

- `tests/test_orchestrator_architecture.py`
- `tests/test_orchestrator_gatekeeper_loop.py`
- `tests/test_orchestrator_task_loop.py`
- `tests/test_orchestrator_mcp_surface.py`
- `tests/test_tui_planning_completion.py`

Assertions to keep or tighten:

- TUI does not infer planning completion from raw tool/runtime events
- interface adapters do not mutate workflow/question stores directly
- review outcomes still route through `TaskLoop`
- MCP compatibility aliases, if temporarily kept, are name-level shims only
- `bootstrap.py` wires owners together but does not become a policy engine

## Workstream 2: Finish Centralizing Policy Helpers

Objective: keep workflow semantics inside the loop packages instead of spreading
them across facade, interface, bootstrap, or `basic/`.

### Gatekeeper loop ownership

Keep these concerns in `policy/gatekeeper_loop/`:

- request shaping in `requests.py`
- question routing and withdrawal rules in `questions.py`
- planning-complete and workflow-transition rules in `transitions.py`
- Gatekeeper-specific session policy in `lifecycle.py`
- Gatekeeper role descriptors and scope rules in `roles.py`

These concerns should not drift back into:

- `basic/`
- `facade.py`
- `interface/policy.py`
- `vibrant/tui/app.py`

### Task loop ownership

Keep these concerns in `policy/task_loop/`:

- ready-task selection and blocking reasons in `selection.py`
- prompt shaping in `prompting.py`
- worker launch policy in `execution.py`
- accept / retry / escalate mapping in `reviews.py`
- task-state to consumer-status projection in `projections.py`
- worker role descriptors and scope rules in `roles.py`

These concerns should not drift into:

- `basic/stores/roadmap.py`
- `basic/stores/reviews.py`
- `basic/workspace/`
- `facade.py`

## Workstream 3: Keep `basic/` Generic and Run-Aware

Objective: finish the reusable mechanics cleanup without moving workflow-specific
code back into `basic/`.

### 3.1 Generic instance/run plumbing

Keep the durable model centered on:

- stable agent instances
- fresh run records beneath those instances
- conversations and runtime events traceable to exact runs

The important constraint is that `basic/` may allocate or persist supplied
instance/run data, but it must not choose concrete roles or scopes on its own.

### 3.2 Runtime, conversation, and binding mechanics

Continue tightening these mechanics so they accept policy-provided launch
descriptors rather than hardcoding workflow semantics:

- `basic/runtime/`
- `basic/conversation/`
- `basic/binding/`
- `basic/workspace/`

The runtime may know how to start, resume, wait, interrupt, and project a run.
It must not decide when Gatekeeper should resume, when a worker should start
fresh, or what a run means to the workflow.

### 3.3 Do not move workflow runners back into `basic/`

These stay policy-owned:

- `policy/gatekeeper_loop/lifecycle.py`
- `policy/task_loop/execution.py`
- role catalogs under `policy/*/roles.py`

That is the key correction to the older plans. Reusable mechanics move down;
workflow-specific runners do not.

## Workstream 4: Thin Interface and Public Surfaces

Objective: make interface code project and dispatch rather than own behavior.

### Interface cleanup

- `interface/policy.py` should forward commands into policy owners
- `interface/basic.py` should expose coherent read models
- `interface/control_plane.py` should compose those surfaces, not duplicate
  workflow semantics
- `interface/mcp/` remains the active MCP implementation

### Facade cleanup

`OrchestratorFacade` should stay as the stable consumer boundary, but it should
not preserve old ownership behavior. Prefer:

- explicit namespaces
- stable snapshots
- thin convenience helpers

Avoid:

- store mutation in facade code
- convenience selection logic that encodes workflow semantics
- exposing raw persistence records as the preferred public contract

### TUI cleanup

The TUI should remain presentation and dispatch only:

- render policy/basic projections
- send user intent through interface/facade commands
- stop inferring planning, question-routing, or review meaning from raw events

## Workstream 5: Delete Residue and Doc Drift

Objective: remove stale paths once callers are moved.

Code cleanup targets:

- shrink root `vibrant/orchestrator/mcp/` to an import/export shim
- remove root module aliases that no longer serve the stable facade
- delete compatibility helpers once all callers use the layered owners

Doc cleanup targets:

- keep architecture guidance in the architecture docs
- remove duplicate orchestrator-only proposal files once their useful content is
  merged
- keep `STABLE_API.md` aligned with the surviving public surface

## Risks

- moving ownership and mechanics in the same patch can obscure review; keep the
  sequencing disciplined
- TUI cleanup can regress planning UX if policy snapshots are not explicit
  enough
- deleting MCP compatibility aliases too early can break prompts or tests that
  still reference old names
- keeping duplicate paths too long makes the architecture harder to read even if
  the behavior still works

## End State Checklist

Phase 2 is complete when all of the following are true:

- `policy/` is the only workflow authority
- `basic/` contains only reusable mechanics and durable plumbing
- workflow-specific runners stay in `policy/`
- `interface/` and `facade.py` do not mutate stores directly
- active MCP implementation lives under `interface/mcp/`
- root compatibility packages are minimal shims
- public snapshots read naturally in role / instance / run terms
- duplicate orchestrator design notes are gone

## Lightweight Verification

Recommended focused checks:

- `uv run pytest tests/test_orchestrator_architecture.py`
- `uv run pytest tests/test_orchestrator_bootstrap.py`
- `uv run pytest tests/test_orchestrator_gatekeeper_loop.py`
- `uv run pytest tests/test_orchestrator_task_loop.py`
- `uv run pytest tests/test_orchestrator_mcp_surface.py`
- `uv run pytest tests/test_tui_planning_completion.py`

Manual sanity checks:

- start the app with `uv run vibrant`
- exercise one planning turn
- exercise one task run
- confirm the MCP tool/resource surface still matches the documented stable
  names
