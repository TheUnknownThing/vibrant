# Orchestrator Stable API

This document defines the **current stable public API** for `vibrant.orchestrator`.

It is meant for external consumers such as the TUI, in-process MCP wiring, and
future integrations that need a durable contract while orchestrator internals
continue to evolve.

## Status

As of **March 11, 2026**, the stable external surface is:

- `OrchestratorFacade`
- `OrchestratorMCPServer`
- `OrchestratorSnapshot`
- `OrchestratorAgentSnapshot`
- `CodeAgentLifecycleResult`

Other package-level exports may exist for compatibility or internal assembly,
but they are **not** stable unless they are listed in this document.

In particular, `OrchestratorEngine`, `OrchestratorStateBackend`,
`TaskDispatcher`, `GitManager`, and `CodeAgentLifecycle` should not be treated
as long-term external contracts even if they are importable today.

## What "Stable" Means Here

For the API listed in this document, the orchestrator should preserve:

- exported names
- method/property availability
- return-type shape and field meanings
- high-level behavior and intent of each operation

The orchestrator may still change:

- internal service composition
- state storage layout
- engine implementation details
- persistence plumbing
- fallback logic retained only for compatibility

Additive evolution is allowed. For example, adding a new helper method or a new
optional field to a model may be acceptable, but removing or redefining the
documented contract should be treated as a breaking change.

## Design Goal

External components should depend on a **small, semantic facade** rather than
reaching into engine-shaped internals such as:

- `OrchestratorEngine`
- `engine.state`
- `engine.consensus`
- `engine.consensus_path`
- internal service instances and their private helpers

This gives the orchestrator freedom to keep refactoring its runtime and storage
implementation without forcing matching changes in every caller.

## Integration Model

The intended integration pattern is:

1. Create or obtain a lifecycle object through app/project bootstrapping.
2. Wrap it in `OrchestratorFacade`.
3. Read state through `snapshot()` or small projection helpers.
4. Mutate state through semantic facade methods.
5. Expose the same facade through `OrchestratorMCPServer` when a scope-gated
   MCP control plane is needed.

The stable consumer contract begins at the facade and the public read models.
The exact shape of the lifecycle object consumed by `OrchestratorFacade(...)`
is intentionally **not** the public integration contract.

## Sync vs Async Surface

Most of the stable facade is synchronous.

The current async methods are:

- `submit_gatekeeper_message(text)`
- `answer_pending_question(answer, *, question=None)`
- `execute_until_blocked()`
- `execute_next_task()`

Everything else documented as part of the stable facade currently uses a normal
synchronous call/return flow.

## Stable Read Models

### `OrchestratorSnapshot`

`OrchestratorSnapshot` is the stable, immutable read model returned by
`OrchestratorFacade.snapshot()`.

It is a frozen dataclass whose collection fields are tuples so consumers can
treat it as a coherent read-only view.

Fields:

- `status: OrchestratorStatus`
  - Current workflow state for the orchestrator.
- `pending_questions: tuple[str, ...]`
  - Pending user-facing question texts in display order.
- `question_records: tuple[QuestionRecord, ...]`
  - Full structured question records, including resolved ones.
- `roadmap: RoadmapDocument | None`
  - Current roadmap document if it has been loaded.
- `consensus: ConsensusDocument | None`
  - Current consensus document if it is available.
- `consensus_path: Path | None`
  - Backing consensus file path when the lifecycle exposes one.
- `agent_records: tuple[AgentRecord, ...]`
  - Durable agent records known to the orchestrator state layer.
- `execution_mode: RoadmapExecutionMode | None`
  - Manual/automatic execution mode when available.
- `user_input_banner: str`
  - User-facing banner text shown when Gatekeeper input is needed.
- `notification_bell_enabled: bool`
  - Whether user-input notification bell behavior is enabled.

Use `snapshot()` when a caller wants a single coherent read of orchestrator
state instead of stitching together several smaller calls.

### `OrchestratorAgentSnapshot`

`OrchestratorAgentSnapshot` is the stable read model returned by facade
agent-query methods.

It represents the orchestrator's unified view of one agent, combining durable
record state with live runtime-handle details when those details are available.

Fields:

- `agent_id: str`
  - Stable identifier for the agent attempt.
- `task_id: str`
  - Roadmap task currently or previously associated with the agent.
- `agent_type: str`
  - Logical type such as `code`.
- `status: str`
  - Durable agent status value.
- `state: str`
  - Runtime-oriented state projection.
- `has_handle: bool`
  - Whether a live runtime handle is currently attached.
- `active: bool`
  - Whether the orchestrator considers the agent active now.
- `done: bool`
  - Whether the agent has completed its lifecycle.
- `awaiting_input: bool`
  - Whether the agent is blocked on user/provider input.
- `pid: int | None`
  - Process identifier when available.
- `branch: str | None`
  - Work branch name when applicable.
- `worktree_path: str | None`
  - Worktree used by the agent when applicable.
- `started_at: datetime | None`
  - Start timestamp.
- `finished_at: datetime | None`
  - Finish timestamp.
- `summary: str | None`
  - Best available summary of the run.
- `error: str | None`
  - Best available terminal error string.
- `provider_thread_id: str | None`
  - Provider-specific thread identifier, if tracked.
- `provider_thread_path: str | None`
  - Path to persisted provider thread data, if tracked.
- `provider_resume_cursor: dict[str, Any] | None`
  - Resume cursor for provider continuation, if tracked.
- `input_requests: list[InputRequest]`
  - Structured input requests currently associated with the run.
- `native_event_log: str | None`
  - Path or identifier for provider-native event logs.
- `canonical_event_log: str | None`
  - Path or identifier for normalized canonical event logs.

### `CodeAgentLifecycleResult`

`CodeAgentLifecycleResult` remains export-stable for compatibility with the
older execution-control flow.

Fields:

- `task_id: str | None`
- `outcome: str`
- `task_status: TaskStatus | None`
- `agent_record: AgentRecord | None`
- `gatekeeper_result: GatekeeperRunResult | None`
- `merge_result: GitMergeResult | None`
- `events: list[CanonicalEvent]`
- `summary: str | None`
- `error: str | None`
- `worktree_path: str | None`

This type is stable as a compatibility result object, but consumers should not
assume every nested runtime detail is the preferred future app-facing contract.
For new app integrations, prefer `OrchestratorSnapshot`,
`OrchestratorAgentSnapshot`, and semantic facade methods.

## `OrchestratorFacade` Stable Contract

`OrchestratorFacade` is the primary stable entry point.

It is the intended long-term boundary for:

- roadmap operations
- consensus operations
- structured question operations
- workflow status and control
- agent/run read projections

### Stable Read API

#### Workflow and document reads

- `snapshot() -> OrchestratorSnapshot`
  - Returns the main stable read model.
- `workflow_status() -> OrchestratorStatus`
  - Convenience projection of `snapshot().status`.
- `consensus_document() -> ConsensusDocument | None`
  - Returns the current consensus document.
- `roadmap() -> RoadmapDocument | None`
  - Returns the current roadmap document.
- `consensus_source_path() -> Path | None`
  - Returns the current consensus path when exposed by the lifecycle/engine.
- `roadmap_document: RoadmapDocument | None`
  - Stable property alias for direct roadmap access.
- `execution_mode: RoadmapExecutionMode | None`
  - Stable property exposing normalized execution mode.

#### Task and roadmap reads

- `task(task_id) -> TaskInfo | None`
  - Returns one task by id or `None` if it does not exist.
- `task_summaries() -> dict[str, str]`
  - Returns the latest known summary text per task id, based on available agent
    record timestamps.

#### Question reads

- `pending_questions() -> list[str]`
  - Returns pending question texts in order.
- `question_records() -> list[QuestionRecord]`
  - Returns all known structured question records.
- `pending_question_records() -> list[QuestionRecord]`
  - Returns only unresolved question records.
- `current_pending_question() -> str | None`
  - Returns the first pending question text or `None`.
- `user_input_banner() -> str`
  - Returns the banner string used to prompt for user action.
- `notification_bell_enabled() -> bool`
  - Returns whether notification bell behavior is enabled.

#### Agent reads

- `agent_records() -> list[AgentRecord]`
  - Returns the durable agent records currently visible through the facade.
- `get_agent(agent_id) -> OrchestratorAgentSnapshot | None`
  - Returns one stable agent snapshot by id.
- `list_agents(*, task_id=None, agent_type=None, include_completed=True, active_only=False) -> list[OrchestratorAgentSnapshot]`
  - Returns filtered stable agent snapshots.
- `list_active_agents() -> list[OrchestratorAgentSnapshot]`
  - Convenience projection equivalent to `list_agents(active_only=True)`.

#### Read semantics

The documented read helpers are stable even if the facade internally serves
them from different sources over time, such as:

- a dedicated service object
- a state store
- a compatibility fallback to legacy engine state

Callers should rely on the return values, not on where the data came from.

### Stable Action API

These are the preferred stable write and workflow-intent entry points for most
external integrations.

#### Consensus and roadmap actions

- `update_consensus(**updates) -> ConsensusDocument`
  - Updates orchestrator-owned consensus fields.
- `add_task(task, *, index=None) -> TaskInfo`
  - Adds one task to the roadmap.
- `update_task(task_id, **updates) -> TaskInfo`
  - Updates one roadmap task.
- `reorder_tasks(ordered_task_ids) -> RoadmapDocument`
  - Reorders tasks by explicit id order.
- `replace_roadmap(*, tasks, project=None) -> RoadmapDocument`
  - Replaces the entire roadmap task list after validation.

`replace_roadmap(...)` is stable for first-party Gatekeeper/MCP flows and other
structured integrations that need whole-document replacement. Generic app code
should still prefer `add_task`, `update_task`, and `reorder_tasks` when they
are sufficient.

#### Question and Gatekeeper actions

- `ask_question(text, *, source_agent_id=None, source_role="gatekeeper", priority=QuestionPriority.BLOCKING) -> QuestionRecord`
  - Creates one structured question record.
- `request_user_decision(...) -> QuestionRecord`
  - Stable alias for `ask_question(...)` used by Gatekeeper-oriented code.
- `set_pending_questions(questions, *, source_agent_id=None, source_role="gatekeeper") -> list[QuestionRecord]`
  - Reconciles the pending question set with a new ordered list of texts.
- `resolve_question(question_id, *, answer=None) -> QuestionRecord`
  - Resolves one structured question record.
- `submit_gatekeeper_message(text) -> Awaitable[Any]`
  - Sends a planning/control message to the Gatekeeper flow.
- `answer_pending_question(answer, *, question=None) -> Awaitable[Any]`
  - Answers the current or specified pending question.

#### Workflow actions

- `pause_workflow() -> None`
  - Transitions the orchestrator into `paused` if it is not already paused.
- `resume_workflow() -> None`
  - Resumes from `paused` into `executing`; raises `ValueError` from other
    states.
- `end_planning_phase() -> OrchestratorStatus`
  - Advances planning-oriented flows into execution-oriented workflow state.

#### Task review actions

- `review_task_outcome(task_id, *, decision, failure_reason=None) -> TaskInfo`
  - Applies a Gatekeeper-style verdict to a completed or failed task.
- `mark_task_for_retry(task_id, *, failure_reason, prompt=None, acceptance_criteria=None) -> TaskInfo`
  - Prepares a task for retry by updating its failure state and moving it back
    to `queued` when possible, otherwise to `escalated`.

These review helpers are stable for the first-party review/MCP workflow.

### Behavioral Notes

The stable contract includes these behavioral expectations:

- `snapshot()` returns an immutable read model; consumers should fetch a new
  snapshot rather than mutating an old one.
- `pause_workflow()` is idempotent when already paused.
- `resume_workflow()` is intentionally stricter and only resumes from the
  paused state.
- `list_agents(include_completed=False)` still includes agents awaiting input.
- `task_summaries()` keeps the newest summary seen for each task id.
- `current_pending_question()` returns the first pending question text, not the
  full question record.

### Error Model

The stable facade may raise standard Python exceptions when a caller asks for
unsupported or invalid behavior.

Current notable cases include:

- `AttributeError`
  - when the underlying lifecycle does not support a requested capability
- `KeyError`
  - when a task id is required but not found for an operation such as review or
    retry preparation
- `ValueError`
  - when a requested transition or decision is invalid in the current state

Consumers should treat these as part of the operational contract.

## Compatibility API

The following facade methods remain available for legacy callers, but they are
**compatibility entry points**, not the preferred long-term contract for new
integrations:

- `reload_from_disk()`
- `execute_until_blocked()`
- `execute_next_task()`
- `can_transition_to(next_status)`
- `transition_workflow_state(next_status)`

These methods expose lower-level runtime-driving or persistence-shaped behavior
that may continue to change as the orchestrator converges on a more explicit
service-backed control plane.

`CodeAgentLifecycleResult` is stable for these compatibility flows, but new code
should avoid depending on raw merge details, Gatekeeper result internals, event
lists, or worktree paths unless that information is later promoted into a more
intentional stable read model.

## `OrchestratorMCPServer` Stable Surface

`OrchestratorMCPServer` is the stable typed in-process MCP registry layered on
top of `OrchestratorFacade`.

It is responsible for:

- listing scope-filtered MCP resources
- listing scope-filtered MCP tools
- enforcing shared authorization scopes from `vibrant.mcp.authz`
- dispatching MCP calls into facade-backed handlers

It is **not** a promise about a specific network transport. The stable contract
is the in-process registry shape and the resource/tool names documented here.

### Stable MCP resources

Current stable resource names are:

- `consensus.current`
- `questions.pending`
- `roadmap.current`
- `task.by_id`
- `workflow.status`

### Stable MCP tools

Current stable tool names are:

- `consensus_get`
- `consensus_update`
- `question_ask_user`
- `question_resolve`
- `roadmap_add_task`
- `roadmap_get`
- `roadmap_reorder_tasks`
- `roadmap_update_task`
- `task_get`
- `workflow_pause`
- `workflow_resume`
- `vibrant.end_planning_phase`
- `vibrant.request_user_decision`
- `vibrant.set_pending_questions`
- `vibrant.review_task_outcome`
- `vibrant.mark_task_for_retry`
- `vibrant.update_consensus`
- `vibrant.update_roadmap`

These names are scope-gated through the shared authz model in
`vibrant/mcp/authz.py` rather than an orchestrator-specific authorization layer.

## Not Stable

The following are **not** stable external APIs and may change during future
refactors:

- `OrchestratorEngine`
- `OrchestratorStateBackend`
- `CodeAgentLifecycle`
- direct access to `engine.state`
- direct access to `engine.consensus`
- direct access to `engine.consensus_path`
- direct access to lifecycle services such as `roadmap_service`,
  `consensus_service`, `question_service`, `workflow_service`, or
  `agent_manager`
- internal packages under `vibrant/orchestrator/agents/`,
  `vibrant/orchestrator/execution/`, `vibrant/orchestrator/artifacts/`, and
  `vibrant/orchestrator/state/`
- internal fallback behavior that exists only to preserve legacy callers

If an external component needs one of these capabilities, prefer promoting that
need into a new facade method or stable read model instead of reaching through
to internals.

## Current Caveat

The TUI no longer reaches through `OrchestratorFacade` to consume raw
engine-shaped state.

Some runtime-driving helpers still exist on the facade while execution control
continues moving toward stable workflow-oriented APIs.

So the current state is:

- a stable facade and stable read models are present
- engine-shaped facade compatibility has been removed from normal consumers
- full execution-surface decoupling is still in progress

## Guidance For Future Refactors

When refactoring the orchestrator system:

1. Preserve `OrchestratorFacade` method names and the documented behavior.
2. Preserve `OrchestratorSnapshot` field meanings.
3. Preserve `OrchestratorAgentSnapshot` field meanings.
4. Prefer semantic intent methods over generic runtime-driver methods.
5. Avoid introducing new external dependencies on engine internals.
6. Add new public needs to the facade before exposing underlying services.
7. Treat `execute_*` helpers as compatibility, not as the preferred direction.

## Minimal Examples

### Read-oriented integration

```python
from vibrant.orchestrator import OrchestratorFacade

facade = OrchestratorFacade(lifecycle)

snapshot = facade.snapshot()
status = facade.workflow_status()
questions = facade.pending_questions()
task = facade.task("task-1")
agents = facade.list_active_agents()
```

### Workflow and question actions

```python
facade.pause_workflow()
facade.resume_workflow()

record = facade.ask_question("Should we proceed?")
resolved = facade.resolve_question(record.question_id, answer="Yes")
```

### Async Gatekeeper actions

```python
await facade.submit_gatekeeper_message("Please refine the roadmap.")
await facade.answer_pending_question("Use the safer migration path.")
```

## Regression Coverage

Compatibility expectations for this stable surface are currently covered by:

- `tests/test_orchestrator_facade.py`
- `tests/test_orchestrator_mcp.py`

Those tests should be extended whenever the documented stable contract changes.
