# Orchestrator Services Plan

> Historical note: this plan predates the `bootstrap.py` composition root and
> the removal of `lifecycle.py` / `engine.py`. File references below describe
> the pre-refactor extraction work.

## Goal

Split the current `CodeAgentLifecycle` coordinator into explicit domain packages under `vibrant/orchestrator/agents/`, `vibrant/orchestrator/execution/`, `vibrant/orchestrator/artifacts/`, and `vibrant/orchestrator/state/`, with `OrchestratorFacade` as the only public entrypoint for the TUI, Gatekeeper integration, and future MCP tools.

The file name is historical; the implementation now uses domain packages rather than a single flat `services/` directory.

This document records:

- what the current lifecycle system manages
- the target service inventory
- what each service should own
- what should remain outside the service layer
- the recommended extraction order

## Current Lifecycle Responsibilities

Today `vibrant/orchestrator/lifecycle.py` is the real coordinator for most orchestrator behavior. It currently manages:

1. project/bootstrap wiring
2. roadmap loading and persistence
3. Gatekeeper message routing
4. workflow execution loops
5. task dispatch
6. execution-agent runtime orchestration
7. prompt assembly and skill loading
8. Gatekeeper review routing
9. retry and escalation policy
10. git worktree and merge handling
11. workflow completion checks

That means the current lifecycle is effectively acting as multiple services at once.

## Design Rules

### Rule 1: Services own domain mutations

Services should perform typed, validated operations against orchestrator state and durable artifacts.

### Rule 2: The facade composes services

`OrchestratorFacade` should be the only API used by:

- TUI
- Gatekeeper integration
- future MCP tools/resources
- external automation

### Rule 3: Durable files remain, but services own writes

The durable human-readable artifacts remain:

- `.vibrant/state.json`
- `.vibrant/consensus.md`
- `.vibrant/roadmap.md`
- `.vibrant/agents/*.json`
- provider event logs

But the service layer should own when and how they are updated.

### Rule 4: Agents do not directly mutate orchestrator-owned artifacts

Gatekeeper and execution agents should report structured intent/results. Services should validate and persist the resulting state.

## Proposed Service Inventory

## 1. `StateStore`

**Target file:** `vibrant/orchestrator/state/store.py`

**Purpose**

Own the durable orchestrator state model and persistence for `.vibrant/state.json`.

**Responsibilities**

- load and save `OrchestratorState`
- run model migration and backward-compatibility logic
- expose read snapshots for the facade
- maintain append-only or bounded recent event history in state if adopted
- coordinate derived projections that belong in state, but not policy decisions

**Should own**

- state file I/O
- atomic writes for state
- migration from legacy fields
- structured question/result/review records in a future phase

**Should not own**

- Gatekeeper execution
- roadmap parsing/writing
- workflow policy
- git operations

**Current logic to migrate**

- durable state persistence from `vibrant/orchestrator/engine.py`
- model migrations from `vibrant/models/state.py`

## 2. `ConsensusService`

**Target file:** `vibrant/orchestrator/artifacts/consensus.py`

**Purpose**

Provide typed operations for reading and updating `consensus.md`.

**Responsibilities**

- read consensus document
- update workflow status in consensus
- append or modify decisions through structured operations
- mirror unresolved blocking questions into the human-readable questions section
- preserve versioning through `ConsensusWriter`

**Should own**

- calls into `ConsensusParser` and `ConsensusWriter`
- typed consensus mutations
- consensus snapshots returned by the facade

**Should not own**

- source-of-truth structured question records
- review decisions
- direct user interaction routing

**Current logic to migrate**

- consensus reads/writes from `vibrant/orchestrator/lifecycle.py`
- workflow status syncing currently performed by TUI and engine

## 3. `RoadmapService`

**Target file:** `vibrant/orchestrator/artifacts/roadmap.py`

**Purpose**

Own roadmap loading, persistence, merge behavior, and typed task mutations.

**Responsibilities**

- load `roadmap.md`
- persist roadmap updates
- merge incoming planner/reviewer roadmap changes
- expose typed task lookup/update helpers
- support add/update/reorder operations for future MCP tools
- keep dispatcher-compatible task state in sync

**Should own**

- roadmap parser/writer integration
- task CRUD/reordering
- task definition merging
- roadmap snapshots returned by the facade

**Should not own**

- execution runtime
- Gatekeeper prompt logic
- git merge policy

**Current logic to migrate**

- roadmap reload/persist/merge logic from `vibrant/orchestrator/lifecycle.py`
- task status updates now spread across lifecycle + dispatcher

## 4. `QuestionService`

**Target file:** `vibrant/orchestrator/artifacts/questions.py`

**Purpose**

Own the lifecycle of user-facing questions.

**Responsibilities**

- create structured question records
- list pending/resolved questions
- answer and resolve questions
- track source agent, source role, timestamps, priority, and answer
- project pending questions to TUI and future MCP resources
- mirror blocking unresolved questions into consensus for human visibility

**Should own**

- question source-of-truth records
- answer flow currently delegated through the engine
- question-related events

**Should not own**

- arbitrary Gatekeeper planning messages
- roadmap mutations
- workflow execution loops

**Current logic to migrate**

- `pending_questions` handling from `vibrant/orchestrator/engine.py`
- question-answer flow currently exposed through engine and lifecycle
- TUI pending-question helpers

## 5. `PlanningService`

**Target file:** `vibrant/orchestrator/artifacts/planning.py`

**Purpose**

Handle high-level Gatekeeper planning conversations and planning-triggered updates.

**Responsibilities**

- route project-start and user-conversation messages to Gatekeeper
- distinguish between planning messages and structured question answers
- accept planning outcomes from Gatekeeper
- pass consensus/roadmap changes through the proper domain services
- provide planning-facing APIs for future MCP Gatekeeper tools

**Should own**

- the non-question portion of `submit_gatekeeper_message`
- Gatekeeper request construction for planning mode
- planning result application orchestration

**Should not own**

- task execution runtime
- git merges
- task retry policy

**Current logic to migrate**

- `submit_gatekeeper_message` from `vibrant/orchestrator/lifecycle.py`

## 6. `AgentRegistry`

**Target file:** `vibrant/orchestrator/agents/registry.py`

**Purpose**

Own durable agent metadata and runtime visibility.

**Responsibilities**

- register agents
- create agent records for code/test/merge execution roles
- persist `AgentRecord` files
- preserve first-registration bookkeeping such as `total_agent_spawns`
- update runtime metadata and provider thread identifiers
- expose durable resume handles and pending-input state to higher layers
- list active/completed/failed agents
- expose agent snapshots, latest summaries, and results to the facade

**Should own**

- `.vibrant/agents/*.json` persistence
- record-construction helpers shared by `CodeAgent`, `MergeAgent`, and future `TestAgent`
- spawn-accounting rules that must happen exactly once per run
- latest status per agent
- agent summaries / agent lookups for UI and MCP

**Should not own**

- provider session execution
- roadmap mutation
- workflow policy

**Current logic to migrate**

- `upsert_agent_record` and agent-derived projections from `vibrant/orchestrator/engine.py`
- task summary lookup patterns currently implemented in the TUI

## 7. `PromptService`

**Target file:** `vibrant/orchestrator/execution/prompts.py`

**Purpose**

Build execution prompts and load supporting skill content.

**Responsibilities**

- build task prompts from task + consensus + roadmap context
- load referenced skill files from `.vibrant/skills`
- keep prompt-generation rules in one place
- support future role-specific prompt builders if needed

**Should own**

- task prompt assembly
- skill file resolution/loading

**Should not own**

- provider execution
- task dispatch
- state persistence

**Current logic to migrate**

- `_build_task_prompt`
- `_load_task_skills`

## 8. `TaskExecutionService`

**Target file:** `vibrant/orchestrator/execution/service.py`

**Purpose**

Run task execution end-to-end for one task or one dispatch loop.

**Responsibilities**

- execute next eligible task
- execute until blocked
- coordinate dispatcher transitions during execution
- create execution agent records
- collect canonical events and execution summary
- hand merge conflicts to a merge-specific agent path instead of treating them as generic task failure
- compose a read-only validation/test agent stage once that path exists
- hand completed/failed work to review and workflow services

**Should own**

- `execute_next_task`
- `execute_until_blocked`
- `_execute_task`
- coordination between dispatcher, runtime, review, and workflow services

**Should not own**

- detailed git implementation
- direct roadmap markdown mutations
- Gatekeeper-specific persistence rules

**Current logic to migrate**

- the main execution loop from `vibrant/orchestrator/lifecycle.py`

## 9. `AgentRuntimeService`

**Target file:** `vibrant/orchestrator/agents/runtime.py`

**Purpose**

Own single-run provider adapter session/thread/turn execution details for orchestrator-managed agents.

**Responsibilities**

- delegate the reusable adapter lifecycle to `vibrant.agents.AgentBase` implementations instead of duplicating it in services
- start provider session
- start thread / resume thread / start turn
- collect canonical events
- translate runtime errors
- surface durable resume metadata from provider threads
- represent `request.opened` / awaiting-input state without forcing every request into failure
- handle request rejection policy for unsupported interactive provider requests
- translate `AgentRunResult` into orchestrator-facing runtime results
- stop adapters safely

**Should own**

- the adapter orchestration block inside `_execute_task`
- the bridge from `AgentBase.run()` to orchestrator result objects
- provider-specific runtime event capture

**Should not own**

- task scheduling
- roadmap policy
- review decisions
- first-write spawn accounting or agent-record factory policy

**Current logic to migrate**

- most of the provider runtime portion of `_execute_task`

**Additional support still required after the `AgentBase` merge**

- preserve `total_agent_spawns` accounting when a run is first registered
- expose `AWAITING_INPUT` and request metadata for interactive or resumable flows
- resume threads from stored provider metadata instead of assuming fresh starts only
- let execution services plug in `MergeAgent` and a future `TestAgent` without re-implementing the runtime loop

## 10. `ReviewService`

**Target file:** `vibrant/orchestrator/execution/review.py`

**Purpose**

Route task completion/failure to Gatekeeper and apply the resulting verdict in typed form.

**Responsibilities**

- build Gatekeeper review requests for completion, failure, escalation
- invoke Gatekeeper reviewer flow
- interpret verdicts in typed form
- persist structured review records in a future phase
- return normalized review outcome to execution/workflow services

**Should own**

- Gatekeeper request builders for review cases
- verdict normalization
- review result application hooks

**Should not own**

- direct roadmap markdown persistence
- execution runtime
- git merge implementation

**Current logic to migrate**

- `_build_gatekeeper_request_for_completion`
- `_build_gatekeeper_request_for_failure`
- `_build_gatekeeper_request_for_escalation`
- `_resolve_gatekeeper_decision`

## 11. `RetryPolicyService`

**Target file:** `vibrant/orchestrator/execution/retry_policy.py`

**Purpose**

Own task retry, requeue, and escalation rules.

**Responsibilities**

- decide whether a failed task retries or escalates
- apply retry bookkeeping
- expose retry policy to execution/review services
- keep retry behavior independent from Gatekeeper transport details

**Should own**

- failure policy now embedded inside `_handle_failure`
- retry vs escalation decisions

**Should not own**

- provider execution
- git merge behavior
- roadmap rendering

**Current logic to migrate**

- `_handle_failure` decision logic

## 12. `GitWorkspaceService`

**Target file:** `vibrant/orchestrator/execution/git_workspace.py`

**Purpose**

Own git worktree, diff, and merge operations through the existing `GitManager`.

**Responsibilities**

- create and clean worktrees
- collect diffs/status for review prompts
- merge accepted task branches
- abort merges on failure

**Should own**

- `_create_fresh_worktree`
- `_cleanup_worktree`
- `_collect_diff`
- merge/abort logic currently used after acceptance

**Should not own**

- review decisions
- task dispatch
- workflow completion policy

**Current logic to migrate**

- git/worktree helpers from `vibrant/orchestrator/lifecycle.py`

## 13. `WorkflowService`

**Target file:** `vibrant/orchestrator/artifacts/workflow.py`

**Purpose**

Own workflow status transitions and completion/reconciliation rules.

**Responsibilities**

- pause/resume/complete workflow
- validate state transitions
- reconcile orchestrator status with consensus/roadmap state
- determine when workflow is complete
- expose workflow snapshot for TUI and MCP

**Should own**

- completion checks
- pause/resume rules
- workflow status mutation APIs

**Should not own**

- provider execution
- detailed roadmap changes
- question source-of-truth persistence

**Current logic to migrate**

- `_maybe_complete_workflow`
- transition logic currently split across engine, TUI, and facade

## What Should Stay Outside the Domain Packages

### `vibrant/orchestrator/facade.py`

This should remain the composition layer only.

It should:

- hold service references
- expose app-facing methods
- return aggregated snapshots
- avoid embedding domain logic where possible

### `vibrant/orchestrator/lifecycle.py`

This should become a temporary compatibility shell.

It should:

- delegate to the domain packages and facade
- preserve existing constructor and public methods during migration
- shrink steadily until it can be removed or renamed

### `vibrant/orchestrator/engine.py`

This should either shrink into a true state-store support module or be absorbed by `StateStore` and `AgentRegistry`.

Short term, keep it thin.
Long term, avoid letting it continue as a mixed persistence + policy layer.

## Recommended Extraction Order

### Phase A: boundary setup

1. establish `artifacts/`, `agents/`, `execution/`, and `state/`
2. add `OrchestratorFacade`
3. route TUI calls through the facade
4. keep lifecycle public API stable

### Phase B: low-risk state/document services

1. extract `RoadmapService`
2. extract `QuestionService`
3. extract `ConsensusService`

### Phase C: planning/review separation

1. extract `PlanningService`
2. extract `ReviewService`
3. extract `WorkflowService`

### Phase D: runtime and execution split

1. extract `PromptService`
2. extract `GitWorkspaceService`
3. extract `AgentRuntimeService`
4. extract `TaskExecutionService`
5. extract `RetryPolicyService`

### Phase E: structured records

1. add structured `QuestionRecord`
2. add structured `TaskResultRecord`
3. add structured `BlockerRecord`
4. add structured `ReviewRecord`
5. update facade snapshots and TUI projections to use structured records

## Suggested Directory Shape

```text
vibrant/orchestrator/
├── facade.py
├── lifecycle.py
├── engine.py
├── git_manager.py
├── task_dispatch.py
├── agents/
│   ├── __init__.py
│   ├── manager.py
│   ├── registry.py
│   ├── runtime.py
│   └── store.py
├── artifacts/
│   ├── __init__.py
│   ├── consensus.py
│   ├── planning.py
│   ├── questions.py
│   ├── roadmap.py
│   └── workflow.py
├── execution/
│   ├── __init__.py
│   ├── git_workspace.py
│   ├── prompts.py
│   ├── retry_policy.py
│   ├── review.py
│   └── service.py
└── state/
    ├── __init__.py
    ├── backend.py
    ├── projection.py
    └── store.py
```

## Immediate Next Steps

1. move question-answer persistence out of `OrchestratorEngine`
2. add `WorkflowService` and move workflow status writes there
3. add `PlanningService` for Gatekeeper message routing
4. extract `ReviewService` from Gatekeeper completion/failure flow
5. extract `GitWorkspaceService` and `PromptService`
6. collapse remaining lifecycle orchestration into `TaskExecutionService`

## Summary

The current lifecycle system is not one service; it is a bundle of orchestration concerns.

The stable target is:

- services own domain behavior
- facade is the public boundary
- lifecycle is temporary compatibility glue
- markdown remains durable, but services own the writes
- structured records replace implicit state inferred from markdown and prompt transcripts
