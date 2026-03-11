# Orchestrator Plan: Centralize Tasks as the Core Workflow State

## Goal

Reorganize the orchestrator so task-oriented logic lives together under a task domain, with tasks as the primary workflow state.

Today the workflow is split across multiple domains:

- task definition and status in `.vibrant/roadmap.md`
- scheduling and execution orchestration in `vibrant/orchestrator/execution/`
- derived workflow projections in `vibrant/orchestrator/state/`
- per-agent execution history in `.vibrant/agents/*.json`
- Gatekeeper review effects applied through a mix of task mutation and state rebuilds

The target model is:

- tasks are the orchestrator’s core workflow objects
- task definition, task state, task scheduling, task execution, and task review live together
- orchestrator-wide state becomes a projection around tasks, not a competing workflow source of truth
- Gatekeeper advances tasks through narrow proceeding actions instead of broad roadmap mutation during execution

## Why Reorganize This Way

The current split by technical concern (`execution/`, `state/`, `artifacts/`) made sense during refactor extraction, but it spreads one workflow concept across too many directories.

That has a few costs:

1. task workflow rules are hard to see in one place
2. task state and task history are not modeled together
3. review semantics are separated from execution semantics even though they are part of one task lifecycle
4. state projections partly duplicate what should be task-owned data
5. Gatekeeper enforcement is harder because the task workflow boundary is not explicit enough

The reorganization should optimize for the main product concept: the workflow progresses by moving tasks through a lifecycle.

## Current State Summary

### Task definition and lifecycle

`vibrant/models/task.py`

- `TaskInfo` currently mixes planning fields and runtime fields
- `TaskLifecycle` enforces status transitions

### Roadmap persistence

`vibrant/orchestrator/artifacts/roadmap.py`
`vibrant/consensus/roadmap.py`

- roadmap markdown is the durable human-readable task artifact
- it currently stores both task definition and simplified runtime status

### Scheduling and execution

`vibrant/orchestrator/execution/dispatcher.py`
`vibrant/orchestrator/execution/service.py`

- dispatcher decides readiness and active work
- execution service drives worktree creation, agent start, completion handling, merge, retry, and escalation
- `TaskExecutionAttempt` is only an in-memory object

### Review and Gatekeeper flow

`vibrant/orchestrator/execution/review.py`
`vibrant/orchestrator/mcp/tools_gatekeeper.py`

- Gatekeeper review is part of the task lifecycle, but the code lives outside a unified task domain
- acceptance is still largely represented as status mutation rather than a first-class review record

### Workflow and projections

`vibrant/orchestrator/state/store.py`
`vibrant/orchestrator/state/backend.py`

- `state.json` stores orchestrator-wide projections such as workflow mode, gatekeeper status, active agents, and pending questions
- some fields, like completed/failed task lists, overlap with what should be derived from task-centric workflow state

## Design Principle

Tasks should be organized as a domain, not just a model.

That means the orchestrator should have one task-focused area that owns:

- task definitions
- task workflow state
- task scheduling
- task execution attempts
- task reviews
- task read models used by the facade, MCP, and TUI

Other orchestrator areas should support that domain rather than divide it.

## Recommended Package Layout

Introduce a dedicated task package and move task-oriented logic under it.

Suggested package:

- `vibrant/orchestrator/tasks/`

Suggested contents:

- `models.py`
- `store.py`
- `workflow.py`
- `dispatcher.py`
- `execution.py`
- `review.py`
- `queries.py`

### `vibrant/orchestrator/tasks/models.py`

Purpose:

- define task-owned workflow records and typed lifecycle enums

Suggested contents:

- `TaskDefinition` or a narrowed `TaskInfo`
- `TaskWorkflowState`
- `TaskRunRecord`
- `TaskReviewRecord`
- task-specific enums such as task phase, run status, review decision

Rule:

- task runtime history should stop being represented only by scattered status fields and agent records

### `vibrant/orchestrator/tasks/store.py`

Purpose:

- own durable task workflow state and task history persistence

Suggested responsibilities:

- load task definitions from roadmap services
- persist current task workflow records
- persist task run and review records
- expose task-centric snapshots and lookup methods

Suggested durable data:

- current task workflow state
- run history
- review history

Recommended first step:

- persist this inside `.vibrant/state.json` while treating the task store as the owner of that portion of the state

### `vibrant/orchestrator/tasks/workflow.py`

Purpose:

- own task lifecycle transitions and workflow rules

Suggested responsibilities:

- compute readiness from dependencies and workflow mode
- transition tasks between `planned`, `ready`, `running`, `awaiting_review`, `awaiting_input`, `accepted`, `retry_ready`, and `escalated`
- create and close run records
- create review records
- validate retry, accept, and escalation rules

This should become the main policy engine for task progression.

### `vibrant/orchestrator/tasks/dispatcher.py`

USER_COMMENT: we should let the gate keeper to decide what is the next task

Purpose:

- choose the next task to run from task workflow state

Suggested responsibilities:

- find executable tasks
- respect dependencies and concurrency
- order candidates by roadmap order and priority

Rule:

- the dispatcher should work from task workflow records, not from ad hoc mixed status projections

### `vibrant/orchestrator/tasks/execution.py`

Purpose:

- run one task through the execution phase using task workflow actions

Suggested responsibilities:

- prepare worktree and execution prompt
- create a durable `TaskRunRecord` before starting the agent
- start the code agent
- record completion or failure back into task workflow state
- hand off to task review logic

This file replaces task-oriented logic currently split between `execution/service.py` and task status mutation paths.

### `vibrant/orchestrator/tasks/review.py`

Purpose:

- route task completion/failure through Gatekeeper and convert the result into typed task review actions

Suggested responsibilities:

- build Gatekeeper review requests for task outcomes
- collect Gatekeeper review decisions
- record durable `TaskReviewRecord` entries
- apply accepted / retry / needs input / escalated transitions through task workflow service

### `vibrant/orchestrator/tasks/queries.py`

Purpose:

- provide stable task-centric read models for the facade, MCP resources, and TUI

Suggested outputs:

- task detail view
- task run history view
- task review history view
- task queue / active / blocked summaries

This keeps read concerns task-centered as well.

## What Stays Outside the Task Package

Not everything should move.

### Keep roadmap markdown in artifacts

`vibrant/orchestrator/artifacts/roadmap.py`

It should remain the human-readable task-definition artifact service.

It should own:

- roadmap parsing and writing
- dependency validation on definitions
- planning-time task definition updates

It should not remain the main owner of runtime task workflow state.

### Keep global orchestrator projections in state

`vibrant/orchestrator/state/`

This package should slim down to orchestrator-wide concerns such as:

- workflow mode
- gatekeeper status
- pending questions
- active provider/runtime metadata
- other global projections not naturally owned by one task

Task-derived data in `state.json` should still exist if useful, but it should be owned conceptually by the task domain.

### Keep MCP transport separate

`vibrant/orchestrator/mcp/`

MCP should remain a transport/boundary layer.

It should call task-domain services instead of owning task workflow semantics directly.

## Recommended Task Model Split

Separate task data into three layers.

### 1. Task definition

Planning-facing fields, persisted in roadmap markdown.

Suggested fields:

- `id`
- `title`
- `acceptance_criteria`
- `prompt`
- `skills`
- `dependencies`
- `priority`
- retry policy fields if they stay part of definition

### 2. Task workflow state

Current state of the task in the workflow.

Suggested fields:

- `task_id`
- `phase`
- `current_run_id`
- `last_run_id`
- `retry_count`
- `failure_reason`
- `accepted_run_id`
- `updated_at`

### 3. Task history

Audit record of what happened.

Suggested records:

- `TaskRunRecord`
- `TaskReviewRecord`

Suggested `TaskRunRecord` fields:

- `run_id`
- `task_id`
- `agent_id`
- `branch`
- `worktree_path`
- `status`
- `started_at`
- `finished_at`
- `summary`
- `error`

Suggested `TaskReviewRecord` fields:

- `review_id`
- `task_id`
- `run_id`
- `reviewer_agent_id`
- `decision`
- `failure_reason`
- `created_at`

## Recommended Task State Machine

Suggested phases:

- `planned`
- `ready`
- `running`
- `awaiting_review`
- `awaiting_input`
- `accepted`
- `retry_ready`
- `escalated`

Recommended meanings:

- `planned` means defined but not executable yet
- `ready` means executable now
- `running` means an active run exists
- `awaiting_review` means execution is done and Gatekeeper review is required
- `awaiting_input` means user input blocks the task
- `accepted` means a reviewed result is approved and merged
- `retry_ready` means another run is permitted
- `escalated` means higher-level intervention is required

This is a better fit than the current `pending/queued/in-progress/completed/accepted/failed/escalated` blend because it reflects workflow meaning directly.

## Recommended Proceeding Actions

Internal task actions should be explicit.

Suggested task actions:

- `execute_task`
- `record_execution_result`
- `verify_execution`
- `accept_result`
- `request_retry`
- `escalate_task`
- `request_task_input`

These should become the language of the task domain even before every action is exposed over MCP.

## MCP Surface Direction

Gatekeeper should use narrow proceeding-style task tools during execution.

Recommended execution-phase tools:

- `vibrant.execute_task(task_id)`
- `vibrant.verify_execution(task_id, run_id, decision, notes=None)`
- `vibrant.accept_result(task_id, run_id)`
- `vibrant.request_task_retry(task_id, run_id, failure_reason, prompt=None, acceptance_criteria=None)`
- `vibrant.escalate_task(task_id, run_id=None, reason)`
- `vibrant.request_task_input(task_id, question)`

Recommended planning-phase tools:

- `vibrant.update_consensus(...)`
- `vibrant.update_roadmap(...)`
- optional add/update/reorder task-definition tools while still in planning mode

### Key rule

In execution mode, Gatekeeper should not have general task-definition mutation powers.

That gives two clean authority modes:

- planning mode: define and reshape tasks
- execution mode: progress tasks through approved proceeding actions

## Authorization Direction

Current scopes are too coarse for the desired behavior.

Suggested future split:

- `tasks:read`
- `tasks:plan:write`
- `tasks:workflow:write`
- `tasks:run`
- `orchestrator:questions:write`
- `orchestrator:workflow:write`

Recommended Gatekeeper default in execution mode:

- `tasks:read`
- `tasks:workflow:write`
- `orchestrator:questions:write`
- `orchestrator:workflow:write`

Recommended Gatekeeper default in planning mode:

- `tasks:read`
- `tasks:plan:write`
- `orchestrator:consensus:write`
- `orchestrator:questions:write`
- `orchestrator:workflow:write`

This makes proceed-only behavior enforceable in authz, not just in prompts.

## Concrete Reorganization Plan

## Phase 1: Create the task domain package

Add:

- `vibrant/orchestrator/tasks/__init__.py`
- `vibrant/orchestrator/tasks/models.py`
- `vibrant/orchestrator/tasks/store.py`
- `vibrant/orchestrator/tasks/workflow.py`
- `vibrant/orchestrator/tasks/dispatcher.py`
- `vibrant/orchestrator/tasks/execution.py`
- `vibrant/orchestrator/tasks/review.py`
- `vibrant/orchestrator/tasks/queries.py`

Initial responsibility:

- establish one obvious home for task-oriented behavior even if some code is temporarily delegated from old services

## Phase 2: Move task models and task state ownership

Primary files:

- `vibrant/models/task.py`
- `vibrant/models/state.py`
- `vibrant/orchestrator/tasks/models.py`
- `vibrant/orchestrator/tasks/store.py`
- `vibrant/orchestrator/state/backend.py`

Deliverables:

- introduce task workflow and history records
- move task-centric persistence ownership behind the task store
- keep dual-write compatibility where needed during migration

## Phase 3: Move scheduling and execution under tasks

Primary files:

- `vibrant/orchestrator/execution/dispatcher.py`
- `vibrant/orchestrator/execution/service.py`
- `vibrant/orchestrator/tasks/dispatcher.py`
- `vibrant/orchestrator/tasks/execution.py`
- `vibrant/orchestrator/tasks/workflow.py`

Deliverables:

- dispatcher reads from task workflow state
- task execution creates durable run records before agent start
- execution result handling writes back through task workflow actions

Migration note:

- old `execution/` modules can temporarily become thin wrappers before removal

## Phase 4: Move review under tasks

Primary files:

- `vibrant/orchestrator/execution/review.py`
- `vibrant/orchestrator/tasks/review.py`
- `vibrant/orchestrator/tasks/workflow.py`
- `vibrant/orchestrator/facade.py`

Deliverables:

- durable typed review records
- accept/retry/escalate become explicit task workflow operations
- review outcomes stop being inferred indirectly from task status reloads

## Phase 5: Make MCP and facade task-centered

Primary files:

- `vibrant/orchestrator/mcp/server.py`
- `vibrant/orchestrator/mcp/tools_gatekeeper.py`
- `vibrant/orchestrator/mcp/resources.py`
- `vibrant/orchestrator/facade.py`
- `vibrant/mcp/authz.py`

Deliverables:

- proceeding-style task tools become first-class MCP operations
- task read resources expose runs, reviews, and workflow state
- planning-write and workflow-write authz are separated

## Phase 6: Slim old packages

After task code is moved:

- `vibrant/orchestrator/execution/` should contain only non-task execution helpers or disappear entirely
- `vibrant/orchestrator/state/` should focus on global orchestrator projections
- `vibrant/orchestrator/artifacts/roadmap.py` should focus on task-definition persistence only

This is the actual end-state reorganization goal.

## Compatibility Strategy

Use an incremental migration.

Recommended order:

1. add the task domain package
2. dual-write old task status and new task workflow records
3. switch scheduling, execution, and review to task-domain reads first
4. simplify old `execution/` and `state/` code once the facade, MCP, and TUI no longer depend on the old split

During the dual-write phase:

- `.vibrant/roadmap.md` may continue to render a simplified human-readable task status
- `.vibrant/state.json` may continue to hold machine-readable task records
- `.vibrant/agents/*.json` remains execution evidence, not the sole workflow truth

## Recommended End State

At the end of this migration:

- task-oriented logic is grouped under `vibrant/orchestrator/tasks/`
- `.vibrant/roadmap.md` is the human-readable task-definition artifact
- task workflow and history are owned by the task domain
- `state.json` stores orchestrator-wide projections around that task-owned state
- MCP tools and facade methods describe task workflow operations directly
- Gatekeeper advances tasks through explicit proceeding tools rather than broad task mutation

## Verification Plan

When implementing this reorganization, verify the following end to end:

1. Create or load a roadmap and confirm task definitions still persist normally.
2. Execute a task and confirm a durable task run exists before the agent finishes.
3. Finish execution and confirm the task moves to `awaiting_review` through task workflow logic.
4. Apply a Gatekeeper accept decision and confirm a review record is persisted and the task becomes `accepted`.
5. Apply a retry decision and confirm a new run can be created without losing prior run history.
6. Restart the orchestrator and confirm task workflow state reconstructs correctly.
7. Confirm execution-mode Gatekeeper principals cannot call planning-only task-definition mutation tools.
8. Confirm facade and MCP resources can answer task-centric queries without reaching into legacy execution/state internals.

Recommended commands during implementation:

- `uv run pytest`
- targeted orchestrator and MCP tests for task workflow, review, and authorization boundaries

