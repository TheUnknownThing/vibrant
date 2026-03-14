# Architecture Layer Refactor Plan

> Date: 2026-03-14
> Status: Proposed
> Scope: internal architecture refactor, no intended user-facing workflow changes in the first pass

## Goal

Refactor the orchestrator from a flat set of peer services into a strict three-layer architecture:

1. `basic` layer: reusable capabilities such as agents, runtime, artifacts, workspace, binding, and event/conversation plumbing
2. `policy` layer: the actual orchestration logic, centered on two loops
   - user <-> Gatekeeper loop
   - task loop: start -> code -> validate -> review/decide -> merge
3. `interface` layer: external surfaces that expose either policy commands or basic read/query access

The main design rule is dependency direction:

- `interface -> policy -> basic`
- `interface -> basic` is allowed for read/query paths, but it must not duplicate policy behavior
- `basic` must not depend on `policy` or `interface`

Naming and API constraints:

- broad renames, file moves, deletions, and total rewrites are allowed when they simplify the architecture
- internal compatibility is not a goal; internal module paths, helper names, and service names may change or disappear
- the `policy` vs `basic` split is internal only
- the only required stable surface is the external facade API, centered on `OrchestratorFacade`

## Rewrite Bias

- prefer moving code into the target package layout and deleting the old path in the same PR over building long-lived compatibility wrappers
- do not preserve old peer-service seams once a caller has been migrated
- delete stale or duplicate scaffolding instead of trying to keep two architectures alive in parallel
- keep only the facade import path and facade semantics stable; everything else is free to change

## Non-Negotiable Rules

- `QuestionStore` is the authoritative user-decision model. Provider-native input requests are transport/runtime signals, not the durable workflow authority.
- only policy code may decide workflow transitions, task transitions, review outcomes, retry/escalate behavior, and whether user input is a new message or an answer
- basic capabilities may persist state and perform mechanics, but they may not decide when those actions should happen
- interface code must stop probing for backend behavior through dotted fallback names once the new adapters exist
- old internal modules are not compatibility boundaries and should be deleted once replaced
- `bootstrap.Orchestrator` should end as a composition root plus minimal facade support, not as the place where real orchestration decisions live
- `OrchestratorFacade` remains the only compatibility boundary that must stay coherent during and after the rewrite

## Current Findings

The repo already contains pieces of this split, but they are still flattened together.

### What already maps cleanly to `basic`

- durable stores in `vibrant/orchestrator/stores/*`
- conversation projection in `vibrant/orchestrator/conversation/*`
- runtime mechanics in `vibrant/orchestrator/runtime/service.py`
- workspace mechanics in `vibrant/orchestrator/workspace.py`
- MCP binding in `vibrant/orchestrator/binding.py`
- provider adapters in `vibrant/providers/*`
- agent builders in `vibrant/agents/*`

### What is still policy logic disguised as peer services

- `vibrant/orchestrator/control_plane.py` currently owns the user <-> Gatekeeper loop
- `vibrant/orchestrator/gatekeeper/lifecycle.py` mixes runtime lifecycle with some session-policy concerns
- `vibrant/orchestrator/workflow/policy.py` owns task selection and task-state transitions
- `vibrant/orchestrator/execution/coordinator.py` still sequences part of the task loop, not just mechanics
- `vibrant/orchestrator/review/control.py` mixes review mechanics with policy outcomes
- `vibrant/orchestrator/bootstrap.py` still contains workflow-driving methods such as `run_next_task()` and `run_until_blocked()`

### What is still interface leakage

- `vibrant/orchestrator/facade.py` exposes raw stores and mutation helpers in addition to the stable facade
- `vibrant/orchestrator/mcp/tools.py` and `vibrant/orchestrator/mcp/resources.py` still use dotted fallback backend resolution
- `vibrant/tui/app.py` decides whether input is a fresh message or an answer to an existing question
- `vibrant/tui/app.py` reaches through the facade into `control_plane` and `runtime_service`

### Concrete behavior gaps to preserve honestly in the first pass

- the Gatekeeper loop is real today
- the task loop is only partially explicit today
  - current flow is `select lease -> start code attempt -> await result -> collect diff -> create review ticket`
  - validation is still a placeholder in `vibrant/orchestrator/execution/coordinator.py`
  - merge is still a placeholder in `vibrant/orchestrator/workspace.py` and `vibrant/orchestrator/review/control.py`
- `vibrant/orchestrator/types.py` already contains validation and merge attempt states, but the runtime path never enters them yet
- `OrchestratorControlPlane.answer_user_decision()` currently resolves a question before submission succeeds; that should be corrected during the Gatekeeper-loop move
- there is partial, stale layering scaffold in the tree already
  - `vibrant/orchestrator/basic/workspace/service.py` duplicates `vibrant/orchestrator/workspace.py`
  - `vibrant/orchestrator/policy/` and `vibrant/orchestrator/interface/` have cache artifacts but not active source modules
  - these partial scaffolds should be deleted or overwritten, not carried as compatibility baggage

That means the refactor should first create the right boundaries, then make the current behavior explicit, and only then deepen validation and merge.

## Recommended Target Shape

Keep `vibrant/orchestrator/` as the stable umbrella package, but organize it hierarchically:

```text
vibrant/orchestrator/
  basic/
    __init__.py
    artifacts.py
    conversations.py
    runtime.py
    workspace.py
    binding.py
    events.py
  policy/
    __init__.py
    models.py
    gatekeeper_loop/
      __init__.py
      state.py
      loop.py
    task_loop/
      __init__.py
      state.py
      loop.py
  interface/
    __init__.py
    backend.py
    basic.py
    policy.py
    control_plane.py
    mcp/
      __init__.py
      resources.py
      tools.py
  facade.py
  bootstrap.py
  __init__.py
  STABLE_API.md
```

With rewrite-first constraints, the old flat root modules should be deleted as soon as callers are migrated. Keep only the root facade module, package exports, and whatever minimal bootstrap entrypoint is needed to construct the facade.

## Layer Responsibilities

### 1. Basic Layer

Owns mechanics and durable state, but not workflow decisions.

Examples:

- artifact stores for roadmap, consensus, attempts, reviews, questions, agents, and workflow state
- conversation storage and projection
- generic agent runtime execution
- provider invocation and MCP binding
- workspace creation, diff collection, merge mechanics
- event log collection
- agent factories and prompt rendering inputs

Rules:

- no task-selection logic
- no review verdict logic
- no Gatekeeper policy decisions
- no TUI or MCP-specific behavior

### 2. Policy Layer

Owns orchestration decisions and loop state.

Two first-class policy components should exist:

- `GatekeeperUserLoop`
  - start/resume the Gatekeeper session
  - record host/user messages into the authoritative conversation
  - send prompts to the Gatekeeper runtime
  - manage question-answer flow and blocking state
  - publish a stable policy snapshot for interfaces
- `TaskLoop`
  - select eligible work
  - create attempts
  - run code stage
  - run validation stage
  - open review/decision stage
  - apply merge stage
  - requeue, escalate, or complete tasks

Supporting policies should either move under the loop modules or become loop-internal helpers.

### 3. Interface Layer

Owns external adapters only.

Internally, interface adapters should separate:

- command-oriented access to policy components
- query/subscription-oriented access to basic capabilities

Consumers:

- TUI uses both surfaces, but only through an adapter
- MCP tools target policy commands
- MCP resources target basic or policy query projections
- `OrchestratorFacade` wraps those internal surfaces rather than talking to stores directly

## Core Contracts To Introduce First

Phase 1 should define these contracts before moving behavior:

- `ArtifactsCapability`
  - bundles typed access to workflow state, roadmap, consensus, attempts, questions, reviews, and agent records
- `ConversationCapability`
  - `bind_agent()`
  - `record_host_message()`
  - `rebuild()`
  - `subscribe()`
- `AgentRuntimeCapability`
  - `start_run()`
  - `resume_run()`
  - `wait_for_run()`
  - `interrupt_run()`
  - `kill_run()`
  - `subscribe_canonical_events()`
- `WorkspaceCapability`
  - `prepare_task_workspace()`
  - `get_workspace()`
  - `collect_review_diff()`
  - `merge_task_result()`
- `BindingCapability`
  - `bind_gatekeeper()`
  - later `bind_worker()` and narrower role bindings
- `EventLogCapability`
  - `record_runtime_event()`
  - `list_recent_events()`
- `PolicyCommandPort`
  - Gatekeeper loop commands
  - task loop commands
  - review resolution commands
- `PolicyQueryPort`
  - policy snapshot
  - Gatekeeper blocking state
  - task-loop progress and review queues

Use `Protocol` or small dataclass wrappers for these contracts. Do not create a large abstract class hierarchy.

## Concrete File Mapping

### Move or rewrite as `basic`

- `vibrant/orchestrator/stores/agents.py`
- `vibrant/orchestrator/stores/attempts.py`
- `vibrant/orchestrator/stores/consensus.py`
- `vibrant/orchestrator/stores/questions.py`
- `vibrant/orchestrator/stores/reviews.py`
- `vibrant/orchestrator/stores/roadmap.py`
- `vibrant/orchestrator/stores/workflow_state.py`
- `vibrant/orchestrator/conversation/store.py`
- `vibrant/orchestrator/conversation/stream.py`
- `vibrant/orchestrator/runtime/service.py`
- `vibrant/orchestrator/workspace.py`
- `vibrant/orchestrator/binding.py`
- `vibrant/providers/*`
- `vibrant/agents/code_agent.py`
- `vibrant/agents/merge_agent.py`
- most of `vibrant/agents/gatekeeper.py` except policy-specific request selection

### Move or rewrite as `policy`

- `vibrant/orchestrator/control_plane.py`
- `vibrant/orchestrator/gatekeeper/lifecycle.py`
- `vibrant/orchestrator/workflow/policy.py`
- `vibrant/orchestrator/execution/coordinator.py`
- `vibrant/orchestrator/review/control.py`
- the workflow-driving parts of `vibrant/orchestrator/bootstrap.py`

### Move or rewrite as `interface`

- `vibrant/orchestrator/mcp/server.py`
- `vibrant/orchestrator/mcp/tools.py`
- `vibrant/orchestrator/mcp/resources.py`
- `vibrant/orchestrator/facade.py`
- the orchestrator-facing parts of `vibrant/tui/app.py`

## Detailed Implementation Plan

## Phase 0: Capture the Current Behavior With Tests

Objective: lock down the behaviors that must survive the package move and expose the ones that should intentionally change.

Work items:

- keep the existing bootstrap, workflow, MCP, and conversation tests green
- add focused regression tests for the architectural seams that will move:
  - Gatekeeper user input routing
  - question-answer failure semantics
  - task-loop stage transitions
  - facade compatibility
  - MCP adapter routing without backend fallback probing
- add one import smoke test for the stable facade export path from `vibrant/orchestrator/__init__.py`

Tests to add or expand:

- `tests/test_orchestrator_bootstrap.py`
- `tests/test_orchestrator_workflow.py`
- `tests/test_orchestrator_mcp_surface.py`
- `tests/test_orchestrator_conversation.py`
- new `tests/test_orchestrator_gatekeeper_loop.py`
- new `tests/test_orchestrator_task_loop.py`
- new `tests/test_orchestrator_facade.py`

Specific assertions to add:

- answering a question does not mark the question resolved if Gatekeeper submission fails
- `run_next_task()` enters explicit validation and review states even when validation is still skipped
- `VibrantApp` no longer needs to inspect pending questions to choose which command to call once the interface migration is done

Exit criteria:

- the repo has a baseline that makes later rewrites safe
- the intended behavior changes are codified before implementation, not after regressions appear

## Phase 1: Establish Layer Contracts and Source Packages

Objective: create the package skeleton and contract types before moving behavior.

Files to create:

- `vibrant/orchestrator/basic/__init__.py`
- `vibrant/orchestrator/basic/artifacts.py`
- `vibrant/orchestrator/basic/conversations.py`
- `vibrant/orchestrator/basic/runtime.py`
- `vibrant/orchestrator/basic/workspace.py`
- `vibrant/orchestrator/basic/binding.py`
- `vibrant/orchestrator/basic/events.py`
- `vibrant/orchestrator/policy/__init__.py`
- `vibrant/orchestrator/policy/models.py`
- `vibrant/orchestrator/policy/gatekeeper_loop/__init__.py`
- `vibrant/orchestrator/policy/gatekeeper_loop/state.py`
- `vibrant/orchestrator/policy/gatekeeper_loop/loop.py`
- `vibrant/orchestrator/policy/task_loop/__init__.py`
- `vibrant/orchestrator/policy/task_loop/state.py`
- `vibrant/orchestrator/policy/task_loop/loop.py`
- `vibrant/orchestrator/interface/__init__.py`
- `vibrant/orchestrator/interface/backend.py`
- `vibrant/orchestrator/interface/basic.py`
- `vibrant/orchestrator/interface/policy.py`
- `vibrant/orchestrator/interface/control_plane.py`

Files to update:

- `vibrant/orchestrator/__init__.py`
- `vibrant/orchestrator/facade.py`
- `vibrant/orchestrator/bootstrap.py`
- `vibrant/orchestrator/types.py`
- `vibrant/orchestrator/STABLE_API.md`

Implementation details:

- define the capability wrappers and interface ports with explicit typing
- keep implementation thin at first; wrappers can delegate to the current store/service objects
- rewrite the root package around the new layout immediately; do not add broad internal shims
- remove or replace the stale duplicate scaffold under `vibrant/orchestrator/basic/workspace/`; do not maintain two `WorkspaceService` implementations

Important constraint:

- this phase should avoid changing external facade behavior, but internal modules can be reorganized aggressively

Exit criteria:

- the new packages exist in source form
- bootstrap can instantiate the new capability wrappers
- the facade import path remains stable
- internal modules are allowed to break as they are replaced

## Phase 2: Extract the Basic Capability Layer

Objective: move storage and mechanics behind policy-neutral capabilities.

Work items:

- wrap the stores as a coherent `ArtifactsCapability` bundle
- wrap the conversation store and stream as `ConversationCapability`
- wrap `AgentRuntimeService` as `AgentRuntimeCapability`
- wrap `WorkspaceService` as `WorkspaceCapability`
- wrap `AgentSessionBindingService` as `BindingCapability`
- move recent-event tracking out of `bootstrap.Orchestrator` into an `EventLogCapability`

Files to modify heavily:

- `vibrant/orchestrator/bootstrap.py`
- `vibrant/orchestrator/stores/__init__.py`
- `vibrant/orchestrator/conversation/__init__.py`
- `vibrant/orchestrator/runtime/__init__.py`
- `vibrant/orchestrator/workspace.py`
- `vibrant/orchestrator/binding.py`
- new `vibrant/orchestrator/basic/*.py`

Rewrite strategy for this phase:

- move the real implementation into the new `basic` modules rather than building a second wrapper layer around old files
- migrate bootstrap and direct call sites quickly, then delete obsolete flat modules or reduce them to local imports only if the facade still needs them briefly
- do not preserve broad legacy attributes on `Orchestrator` unless the facade still depends on them in the same phase

Rules:

- do not move workflow selection or review decisions into the new basic package
- do not let basic wrappers call back into policy objects

Exit criteria:

- the composition root wires `basic` capabilities first
- the new `basic` package is the primary implementation, not a mirror of the old layout
- tests still pass without interface changes

## Phase 3: Build the Gatekeeper User Loop Policy

Objective: consolidate the user <-> Gatekeeper flow into one policy module.

Policy shape:

- `GatekeeperLoopState`
  - session snapshot
  - authoritative conversation id
  - pending blocking-question projection
  - last submission metadata
  - busy/error state
- `GatekeeperUserLoop`
  - `submit_user_input(text: str, question_id: str | None = None)`
  - `restart(reason: str | None = None)`
  - `stop()`
  - `snapshot()`
  - `conversation(conversation_id: str)`
  - `subscribe_conversation(...)`

Behavior to move into this loop:

- resume/start Gatekeeper session
- bind the Gatekeeper to its conversation
- record user and system messages
- submit user messages and question answers
- track Gatekeeper lifecycle state for interfaces
- own the decision of `new message` vs `answer existing question`
- surface blocking questions and resume semantics

Concrete code moves:

- keep `GatekeeperLifecycleService` as a lower-level runtime helper
- move `OrchestratorControlPlane.submit_user_message()` behavior into `GatekeeperUserLoop`
- move `OrchestratorControlPlane.answer_user_decision()` behavior into `GatekeeperUserLoop`
- move the input-routing branch from `vibrant/tui/app.py:_start_gatekeeper_message()` into policy
- delete `vibrant/orchestrator/control_plane.py` once the facade and TUI no longer need the old path, or keep only a tiny internal adapter if that still helps bootstrap

Required semantic fix in this phase:

- change question resolution ordering so a question is resolved only after Gatekeeper submission is accepted
- on submission failure, the question remains pending and the failure is surfaced to the interface snapshot

Recommended implementation order inside the phase:

1. create `GatekeeperLoopState`
2. wrap existing lifecycle service and conversation capability
3. move submission logic into `GatekeeperUserLoop`
4. adapt the facade/internal command adapter to delegate
5. update the facade and TUI call sites to use the delegated command path

Exit criteria:

- the user/Gatekeeper loop lives in one policy module
- TUI no longer decides the authoritative message-vs-answer path
- failure semantics around pending questions are correct and tested

## Phase 4: Build the Task Loop Policy

Objective: make the task pipeline explicit as one policy state machine.

Target task stages:

1. select/start
2. code
3. validate
4. review/decision
5. merge
6. accepted or retry/escalate

State model to add:

- `TaskLoopStage`
  - `idle`
  - `coding`
  - `validating`
  - `review_pending`
  - `merge_pending`
  - `blocked`
  - `completed`
- `TaskLoopSnapshot`
  - active lease
  - active attempt id
  - current stage
  - pending review ticket ids
  - blocking reason

Concrete refactor steps:

- move `WorkflowPolicyService.select_next()` and transition logic under `policy/task_loop`
- shrink `ExecutionCoordinator` into mechanics only
  - start code run
  - wait for code run
  - return completion artifacts
- move `bootstrap.run_next_task()` and `bootstrap.run_until_blocked()` into `TaskLoop`
- route review resolution through task-loop transitions rather than direct multi-owner state mutations
- make validation an explicit stage even while it still returns a placeholder `ValidationOutcome(status=\"skipped\")`
- make merge an explicit stage even while `WorkspaceService.merge_task_result()` remains placeholder behavior
- delete or fully rewrite `workflow/policy.py`, `review/control.py`, and `execution/coordinator.py` after the new state machine owns the flow

Files to rewrite or wrap:

- `vibrant/orchestrator/workflow/policy.py`
- `vibrant/orchestrator/execution/coordinator.py`
- `vibrant/orchestrator/review/control.py`
- `vibrant/orchestrator/bootstrap.py`
- `vibrant/orchestrator/types.py`

Required cleanups:

- stop duplicating task-state updates across workflow policy, review control, bootstrap helpers, and facade helpers
- choose one reducer-owned path for:
  - attempt started
  - attempt completed
  - review ticket created
  - review accepted
  - review retried
  - review escalated
  - workflow completed

Important scope constraint:

- this phase should model the system as it actually works now
- do not pretend validation or merge are fully implemented
- the win is the explicit stage boundary, not feature completeness

Exit criteria:

- the task loop is a named policy object with an explicit snapshot/state machine
- `bootstrap.Orchestrator` delegates task advancement to that policy object
- review and merge transitions stop being spread across peer services

## Phase 5: Rebuild External Interfaces on Top of Policy and Basic

Objective: make external surfaces depend on explicit adapters instead of the flat orchestrator object.

Adapters to introduce:

- `interface/policy.py`
  - command adapter over `GatekeeperUserLoop` and `TaskLoop`
- `interface/basic.py`
  - read/query adapter over artifacts, conversations, agents, runtime, and event log
- `interface/backend.py`
  - bundles command and query adapters for first-party consumers
- `interface/control_plane.py`
  - internal command/query composition adapter used by the facade and MCP layer

Interface migrations:

- `OrchestratorFacade` should delegate all writes through policy commands
- `OrchestratorFacade.snapshot()` should read from coherent projections, not raw ad hoc store access
- `OrchestratorMCPTools` should call explicit adapter methods, not `call_backend()` dotted fallback chains
- `OrchestratorMCPResources` should read from explicit adapter methods, not peer-store fallbacks
- `vibrant/tui/app.py` should use the facade or a small controller adapter instead of raw `orchestrator.control_plane` and `orchestrator.runtime_service`

Specific TUI changes to make in this phase:

- replace direct calls to `_orchestrator.control_plane.submit_user_message()` and `.answer_user_decision()` with one facade/controller command
- move pending-question routing into the policy/facade layer
- keep TUI responsibilities to presentation, subscriptions, and input dispatch
- keep the chat panel bound to the orchestrator-owned conversation stream, not provider logs

Specific MCP changes to make in this phase:

- preserve MCP tool/resource names
- preserve compatibility aliases at the MCP name layer only
- remove backend-introspection fallback lists once explicit adapters exist

Exit criteria:

- first-party consumers talk to interface adapters only
- interface code stops reaching into stores directly for mutations
- MCP and TUI are thin surfaces over policy/basic contracts

## Phase 6: Collapse Compatibility Paths and Finish the Cleanup

Objective: remove duplicate paths after interfaces are using the new contracts.

Cleanup work:

- reduce `bootstrap.Orchestrator` to composition root plus minimal facade support
- remove duplicate mutation helpers from `bootstrap.py` and `facade.py`
- remove dead peer-service wiring that no longer owns primary behavior
- align `vibrant/orchestrator/STABLE_API.md` with the actual adapter and snapshot names
- delete stale scaffold files and package remnants that would keep the new architecture ambiguous
- delete the old flat-layer modules outright once the facade no longer routes through them

Compatibility policy at the end of this phase:

- keep `OrchestratorFacade` as the stable first-party API
- keep the facade import path stable from `vibrant.orchestrator` and `vibrant/orchestrator/facade.py`
- keep only the minimal bootstrap entrypoint required to construct and back the facade
- do not preserve internal compatibility for abandoned module layouts, service names, or helper methods

Exit criteria:

- there is one authoritative path for each mutation and read model
- the flat peer-service design no longer exists in practice, not just in folder names

## Suggested PR Breakdown

Split the implementation into small reviewable branches:

1. PR 1: tests + target skeleton
   - add missing regression tests
   - create `basic/`, `policy/`, and `interface/` source packages
   - rewrite root exports around the new layout
2. PR 2: basic-layer rewrite
   - move store/runtime/workspace/binding/event wiring into the new `basic` package
   - delete duplicate or stale legacy implementations as call sites move
3. PR 3: Gatekeeper-loop rewrite
   - introduce `GatekeeperUserLoop`
   - fix question-resolution ordering
   - remove the old control-plane message flow
4. PR 4: task-loop rewrite
   - introduce `TaskLoop`
   - move `run_next_task()` and review transitions
   - make validation/merge explicit stages
   - delete old workflow/review ownership paths
5. PR 5: interface rewrite
   - rewrite facade, MCP adapters, and TUI integration against the new ports
   - preserve facade behavior while deleting internal fallback paths
6. PR 6: hard cleanup
   - remove remaining flat-layer modules
   - tighten stable API docs
   - leave only the new architecture plus the stable facade surface

This order still keeps the repo runnable, but it is intentionally biased toward replacement and deletion rather than prolonged dual-path migration.

## Verification

Automated verification after each phase:

- run the focused orchestrator suite first:
  - `uv run pytest tests/test_orchestrator_bootstrap.py`
  - `uv run pytest tests/test_orchestrator_workflow.py`
  - `uv run pytest tests/test_orchestrator_conversation.py`
  - `uv run pytest tests/test_orchestrator_mcp_surface.py`
- run the new focused loop and facade tests once they exist:
  - `uv run pytest tests/test_orchestrator_gatekeeper_loop.py`
  - `uv run pytest tests/test_orchestrator_task_loop.py`
  - `uv run pytest tests/test_orchestrator_facade.py`
- then run the full suite:
  - `uv run pytest`

Manual verification before final cleanup:

1. Start the app with `uv run vibrant` in a test project.
2. Submit an initial planning message and confirm the Gatekeeper conversation still appears in the chat panel.
3. Trigger a pending question, answer it, and verify the answer path works through the interface layer instead of TUI-owned branching.
4. Force a submission failure and confirm the question remains pending.
5. End planning, run one task, and verify the explicit task stages progress through code -> validate -> review.
6. Resolve a review ticket with `accept`, `retry`, and `escalate` in separate runs and verify roadmap/attempt state transitions stay coherent.
7. Exercise the MCP surface and confirm tools/resources still expose the same semantic names while delegating through the new adapters.

## Main Risks

- `bootstrap.py` currently mixes composition root, policy helpers, and interface compatibility. Splitting it too late will keep the new layers cosmetic.
- `facade.py` and `mcp/tools.py` currently depend on broad backend method availability. Without explicit ports, the old coupling will just move files.
- the task loop currently lacks real validation and merge mechanics. If the refactor does not create explicit stages now, those stages will be flattened again later.
- `vibrant/tui/app.py` currently assumes a broad orchestrator object. Introduce a thin controller adapter instead of teaching the TUI every new internal package.
- failure and retry semantics are currently duplicated across workflow policy, review control, bootstrap helpers, and facade helpers. Define one reducer-owned transition path before moving more files.
- stale partial layering scaffolds already exist in the tree. Reusing them blindly will create duplicate implementations instead of a clean layered architecture.
- rewrite-first work can produce large diffs. Keep the PR boundaries disciplined so review remains about architecture and behavior, not file churn alone.

## Deferred Work After This Refactor

Not part of the first architectural pass:

- replacing placeholder validation with real validators
- replacing placeholder merge behavior with real merge/conflict handling
- introducing richer worker/validator/merge-agent MCP bindings
- reconsidering whether the facade itself should later become thinner or narrower after the rewrite settles
