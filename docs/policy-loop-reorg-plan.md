# Policy Loop Reorganization Plan

> Date: 2026-03-15
> Status: proposed
> Scope: reorganize `vibrant/orchestrator/policy/gatekeeper_loop` and `vibrant/orchestrator/policy/task_loop` so each file owns one clearer responsibility without changing workflow semantics first

## Summary

The current top-level split is directionally correct:

- `gatekeeper_loop/` owns planning and user-interaction policy
- `task_loop/` owns execution and review policy

The main problem is inside those packages. `gatekeeper_loop/loop.py` is both a
submission flow and a planning command surface. `task_loop/loop.py` is a large
multi-phase state machine that owns dispatch, recovery, completion handling,
review decisions, merge handling, and task-state projection.

This plan keeps the two loop packages, but divides responsibility harder inside
them. The first pass should be a pure reorganization with behavior preserved.

## Why This Exists

The current shape has three kinds of drift:

1. package boundaries are mostly correct, but file boundaries are too broad
2. runtime-launch mechanics are duplicated between Gatekeeper and task
   execution flows
3. some compatibility leftovers and dead branches still blur the real design

The goal is not to add a new abstraction layer. The goal is to make the
existing policy split easier to reason about, easier to test, and harder to
break accidentally.

## Current Problems

### Gatekeeper package

- [vibrant/orchestrator/policy/gatekeeper_loop/loop.py](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/gatekeeper_loop/loop.py) owns:
  - user submission routing
  - waiting for Gatekeeper completion
  - question creation and withdrawal
  - workflow status transitions
  - roadmap mutations
  - consensus mutations
- [vibrant/orchestrator/policy/gatekeeper_loop/transitions.py](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/gatekeeper_loop/transitions.py) mixes workflow transition helpers with UI-specific transition planning.
- [vibrant/orchestrator/policy/gatekeeper_loop/state.py](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/gatekeeper_loop/state.py) is a compatibility re-export, not a real responsibility boundary.

### Task package

- [vibrant/orchestrator/policy/task_loop/loop.py](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/task_loop/loop.py) owns too many phases:
  - task selection
  - dispatch leasing
  - attempt recovery
  - attempt completion handling
  - review-ticket creation
  - review decision resolution
  - merge acceptance flow
  - task-state projection
  - workflow completion checks
- [vibrant/orchestrator/policy/task_loop/execution.py](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/task_loop/execution.py) overlaps structurally with Gatekeeper lifecycle launch logic.
- [vibrant/orchestrator/policy/task_loop/sessions.py](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/task_loop/sessions.py) still combines:
  - live runtime projection
  - durable attempt/run binding updates
  - internal recovery selection
  - public attempt view shaping

### Cross-package issues

- Gatekeeper and task runtime paths both:
  - bind MCP access
  - compile provider invocation
  - start or resume a run
  - monitor the runtime handle
  - unregister the binding on completion
- worker attempt completion has contract drift:
  - task execution already maps worker `awaiting_input` into failure
  - task-loop completion code still contains branches for unreachable statuses

## Boundary Rules

Use these rules when moving code:

- `gatekeeper_loop/` owns planning semantics and user-to-Gatekeeper routing.
- `task_loop/` owns execution sequencing, review decisions, and task
  progression.
- `shared/` may hold policy helpers reused by both packages, but it must not
  become a dumping ground for generic utilities.
- `basic/` may launch, bind, persist, project, and merge; it must not decide
  workflow meaning.
- `interface/` may translate UI actions into policy commands, but UI-specific
  state-transition planning should not live in a Gatekeeper-only module.
- A file should own one policy concern, not an entire package-worth of
  behavior.

## Target Package Shape

### Gatekeeper package

Target layout:

```text
policy/gatekeeper_loop/
  __init__.py
  commands.py
  lifecycle.py
  models.py
  questions.py
  requests.py
  submission.py
  roles.py
```

Responsibilities:

- `submission.py`
  - submit user input
  - associate input with pending questions
  - wait for Gatekeeper completion
  - build Gatekeeper loop snapshots
  - expose conversation access helpers
- `commands.py`
  - request or withdraw questions
  - mutate workflow status
  - mutate roadmap
  - mutate consensus
- `lifecycle.py`
  - own the single durable Gatekeeper runtime session
  - launch/resume/stop/restart the Gatekeeper run
  - persist lifecycle state from runtime events
- `questions.py`
  - question-scope normalization and pending-question selection
- `requests.py`
  - translate submission context into typed `GatekeeperRequest`
- `roles.py`
  - stable Gatekeeper role and instance policy
- `models.py`
  - Gatekeeper-specific DTOs only

Notes:

- `GatekeeperUserLoop` may remain as the stable public façade type for
  compatibility, but it should delegate to `submission.py` and `commands.py`
  instead of continuing to own all behavior directly.
- Delete `state.py` after imports move.

### Task package

Target layout:

```text
policy/task_loop/
  __init__.py
  attempts.py
  dispatch.py
  execution.py
  models.py
  prompting.py
  reviews.py
  roles.py
  sessions.py
  task_projection.py
```

Responsibilities:

- `dispatch.py`
  - decide whether execution is blocked
  - compute available concurrency slots
  - select eligible tasks
  - create dispatch leases
- `attempts.py`
  - run the next task
  - recover active attempts
  - wait for attempt completion
  - interpret attempt completion into next policy action
- `reviews.py`
  - create review tickets
  - accept/retry/escalate review tickets
  - handle merge acceptance flow
- `task_projection.py`
  - translate `TaskState` <-> `TaskStatus`
  - update retry counts and failure reasons
  - decide whether workflow is complete
- `execution.py`
  - own worker runtime launch/resume mechanics only
- `sessions.py`
  - own attempt session projection and recovery-state computation
  - if needed, split later into internal recovery and public view modules
- `prompting.py`
  - build task prompts and retry patches
- `roles.py`
  - stable task-agent role and instance policy
- `models.py`
  - task-loop DTOs only

Notes:

- `TaskLoop` may remain as the stable public façade type for compatibility, but
  it should delegate to focused helpers instead of owning every state-machine
  phase.
- Delete `state.py` after imports move.

## Shared Runtime Launch Cleanup

Create one shared helper for the repeated runtime-launch mechanics currently
duplicated in Gatekeeper and worker execution paths.

Candidate location:

- `vibrant/orchestrator/policy/shared/runtime_launch.py`

Responsibilities:

- require bound MCP bridge dependencies
- bind an access preset to one run
- register and unregister the binding with the loopback MCP host
- compile the provider invocation plan
- start or resume the provider run
- return the runtime handle plus registered binding metadata

Keep policy-specific responsibilities out of this helper:

- which preset to bind
- how to build the prompt
- how to persist package-specific lifecycle/session state
- how to interpret runtime results

## Compatibility Strategy

The reorganization should not break the current public shape on the first pass.

Keep these types and exports stable initially:

- `GatekeeperLifecycleService`
- `GatekeeperUserLoop`
- `ExecutionCoordinator`
- `TaskLoop`
- existing package `__init__` exports

The first implementation pass should move logic behind these names rather than
renaming the top-level API immediately.

## Migration Sequence

### Phase 1: Correct internal boundaries without behavior changes

1. Extract Gatekeeper submission flow from `gatekeeper_loop/loop.py` into
   `submission.py`.
2. Extract Gatekeeper planning mutations from `gatekeeper_loop/loop.py` into
   `commands.py`.
3. Extract task dispatch selection from `task_loop/loop.py` into `dispatch.py`.
4. Extract task-state projection and workflow-completion helpers into
   `task_projection.py`.
5. Extract review-ticket and merge-acceptance logic into `reviews.py`.
6. Extract attempt execution/recovery/completion logic into `attempts.py`.
7. Keep the public façade classes and delegate to the new helpers.

Acceptance criteria:

- no user-visible behavior change
- existing tests still pass after internal moves
- `GatekeeperUserLoop` and `TaskLoop` get materially smaller

### Phase 2: Remove duplicated runtime launch logic

1. Introduce a shared runtime-launch helper under `policy/shared/`.
2. Move the duplicated MCP binding and provider invocation startup flow out of:
   - [vibrant/orchestrator/policy/gatekeeper_loop/lifecycle.py](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/gatekeeper_loop/lifecycle.py)
   - [vibrant/orchestrator/policy/task_loop/execution.py](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/task_loop/execution.py)
3. Keep run-specific persistence and result interpretation in the owning
   package.

Acceptance criteria:

- one shared launch path owns MCP binding registration and invocation-plan
  compilation
- Gatekeeper and worker policies still control their own lifecycle semantics

### Phase 3: Tighten contracts and delete leftovers

1. Remove unreachable or stale task-loop branches tied to no-longer-emitted
   statuses.
2. Move UI-only transition planning out of
   [vibrant/orchestrator/policy/gatekeeper_loop/transitions.py](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/gatekeeper_loop/transitions.py)
   into `interface/` or a workflow UI adapter.
3. Delete compatibility re-export files:
   - [vibrant/orchestrator/policy/gatekeeper_loop/state.py](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/gatekeeper_loop/state.py)
   - [vibrant/orchestrator/policy/task_loop/state.py](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/task_loop/state.py)
4. Delete compatibility helpers that add no independent meaning, such as
   `build_user_input_plan()`, if no callers remain.

Acceptance criteria:

- package files map cleanly to one responsibility each
- no dead status branches remain in the task execution path
- no compatibility files remain without live callers

## Suggested Ownership Matrix

| Concern | Owning module |
|---|---|
| Gatekeeper runtime session | `gatekeeper_loop/lifecycle.py` |
| Gatekeeper request shaping | `gatekeeper_loop/requests.py` |
| Gatekeeper user submission flow | `gatekeeper_loop/submission.py` |
| Gatekeeper planning commands | `gatekeeper_loop/commands.py` |
| Question normalization and lookup | `gatekeeper_loop/questions.py` |
| Task dispatch policy | `task_loop/dispatch.py` |
| Task attempt orchestration | `task_loop/attempts.py` |
| Task review and merge decisions | `task_loop/reviews.py` |
| Task state projection | `task_loop/task_projection.py` |
| Worker runtime assembly | `task_loop/execution.py` |
| Attempt session projection/recovery | `task_loop/sessions.py` |
| Shared provider run launch mechanics | `policy/shared/runtime_launch.py` |

## Risks

Main risks during this refactor:

- accidentally changing workflow behavior while moving code
- introducing circular imports between new helper modules
- moving UI-specific helpers too early and breaking façade or TUI callers
- widening shared helpers until they start owning policy semantics

Mitigations:

- preserve façade classes and route through delegates first
- move one concern at a time and keep tests green after each extraction
- prefer passing narrow typed values into helpers instead of whole loop objects
- add focused tests around task dispatch, review resolution, and Gatekeeper
  submission routing before deleting compatibility paths

## Verification

Verify all of the following after the reorganization:

- submitting user input still routes correctly as either a fresh message or a
  question answer
- pending questions still block task execution only for the intended scopes
- Gatekeeper restart and stop flows still preserve the correct durable session
  state
- task selection still respects dependency ordering and concurrency limits
- active attempt recovery still works after process restart
- successful attempts still create review tickets
- accept/retry/escalate review decisions still update attempt and task state
  consistently
- merge acceptance still preserves the same behavior as before the refactor
- no public control-plane or facade caller needs to know about the internal
  file moves

## Non-Goals

This plan does not include:

- redesigning the overall `policy/` vs `basic/` architecture
- changing workflow semantics
- changing public control-plane APIs in the first pass
- introducing validation-agent or merge-agent redesign work
- keeping compatibility shims forever after callers are migrated

## Recommended First Change

Start with the lowest-risk extraction:

1. split `gatekeeper_loop/loop.py` into `submission.py` and `commands.py`
2. keep `GatekeeperUserLoop` as a thin façade over those helpers
3. land that with no behavior changes
4. then split `task_loop/loop.py` by dispatch, attempts, reviews, and task
   projection

That sequence reduces file size and responsibility breadth early without
touching the most failure-prone runtime-launch code first.
