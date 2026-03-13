# Orchestrator Stable API

This document defines the current stable public boundary for
`vibrant.orchestrator` as of **March 13, 2026**.

The orchestrator now exposes the architecture directly through layered nouns:

- `workflow`
- `role`
- `instance`
- `run`
- `document`
- `task`
- `question`

The stable consumer contract begins at `OrchestratorFacade` and the exported
read/result models described below. Internal service classes, persistence
records, and storage layout are not part of the stable contract.

## Stable Exports

The stable package-level exports are:

- `OrchestratorFacade`
- `OrchestratorMCPServer`
- `OrchestratorSnapshot`
- `WorkflowSnapshot`
- `DocumentSnapshot`
- `RoleSnapshot`
- `AgentRoleSnapshot`
- `AgentInstanceSnapshot`
- `AgentRunSnapshot`
- `QuestionAnswerResult`
- `TaskExecutionResult`
- `TaskResult`

`TaskResult` remains export-stable as a compatibility alias for
`TaskExecutionResult`.

## Root Facade

`OrchestratorFacade` is the stable root object.

Canonical namespaces:

- `facade.workflow`
- `facade.roles`
- `facade.instances`
- `facade.runs`
- `facade.documents`
- `facade.tasks`
- `facade.questions`

Optional convenience alias:

- `facade.gatekeeper.submit(text)`

Additional UI-facing convenience helpers on the root facade:

- `list_command_history(*, limit=None) -> list[str]`
- `record_command_history_entry(text, *, limit=None) -> list[str]`

The Gatekeeper alias exists only to start a new conversational Gatekeeper turn.
Gatekeeper state and history are exposed through the generic `instances` and
`runs` namespaces.

## Stable Read Models

### `OrchestratorSnapshot`

`OrchestratorFacade.snapshot()` returns `OrchestratorSnapshot`.

Canonical fields:

- `workflow: WorkflowSnapshot`
- `documents: DocumentSnapshot`
- `questions: tuple[QuestionRecord, ...]`
- `roles: tuple[RoleSnapshot, ...]`
- `instances: tuple[AgentInstanceSnapshot, ...]`

Compatibility fields retained on the same snapshot:

- `status`
- `pending_questions`
- `question_records`
- `roadmap`
- `consensus`
- `consensus_path`
- `execution_mode`
- `user_input_banner`
- `notification_bell_enabled`

### `WorkflowSnapshot`

Fields:

- `status: OrchestratorStatus`
- `execution_mode: str | None`
- `user_input_banner: str`
- `notification_bell_enabled: bool`

### `DocumentSnapshot`

Fields:

- `roadmap: RoadmapDocument | None`
- `consensus: ConsensusDocument | None`
- `consensus_path: Path | None`

### `RoleSnapshot`

`RoleSnapshot` and `AgentRoleSnapshot` are the same stable model.

Fields:

- `role`
- `display_name`
- `workflow_class`
- `default_provider_kind`
- `default_runtime_mode`
- `supports_interactive_requests`
- `persistent_thread`
- `ui_model_name`

Additional compatibility metadata may also be present.

### `AgentInstanceSnapshot`

Canonical fields:

- `agent_id`
- `role`
- `scope_type`
- `scope_id`
- `provider_defaults`
- `supports_interactive_requests`
- `persistent_thread`
- `latest_run_id`
- `active_run_id`
- `active`
- `awaiting_input`
- `latest_run`

Compatibility fields retained on the same snapshot:

- `identity`
- `runtime`
- `workspace`
- `outcome`
- `provider`

### `AgentRunSnapshot`

Canonical fields:

- `run_id`
- `agent_id`
- `task_id`
- `role`
- `lifecycle`
- `runtime`
- `workspace`
- `provider`
- `envelope`
- `payload`

Compatibility fields retained on the same snapshot:

- `identity`
- `context`
- `outcome`
- `retry`
- `state`
- `summary`
- `error`

Stable callers should prefer `envelope` and `payload` over persistence-shaped
substructures.

### `QuestionRecord`

Questions are durable orchestrator-owned artifacts.

Stable question linkage fields now include:

- `source_run_id`
- `resolved_by_run_id`

This makes the relationship between a Gatekeeper run and a durable user-facing
question explicit.

### `QuestionAnswerResult`

`facade.questions.answer(...)` returns:

- `question: QuestionRecord`
- `gatekeeper_run: AgentRunSnapshot`

Answering a question is an orchestrator-owned action that starts a new
Gatekeeper run. It is not modeled as a response to an old provider request.

### `TaskExecutionResult`

Canonical fields:

- `task_id`
- `task_status`
- `workflow_outcome`
- `agent_id`
- `run_id`
- `summary`
- `error`
- `payload`

Compatibility fields retained through `TaskResult`:

- `outcome`
- `agent_record`
- `gatekeeper_result`
- `merge_result`
- `events`
- `worktree_path`
- `role_result`

New callers should prefer `workflow_outcome` and `payload`.

## Facade Namespaces

### `workflow`

Read/control methods:

- `status() -> OrchestratorStatus`
- `snapshot() -> WorkflowSnapshot`
- `pause() -> None`
- `resume() -> None`
- `end_planning() -> OrchestratorStatus`
- `execute_next_task() -> TaskExecutionResult | None`
- `execute_until_blocked() -> list[TaskExecutionResult]`

### `roles`

Read methods:

- `get(role) -> RoleSnapshot | None`
- `list() -> list[RoleSnapshot]`

### `instances`

Read/control methods:

- `get(agent_id) -> AgentInstanceSnapshot | None`
- `list(*, task_id=None, role=None, include_completed=True, active_only=False) -> list[AgentInstanceSnapshot]`
- `active() -> list[AgentInstanceSnapshot]`
- `output(agent_id) -> AgentOutput | None`
- `wait(agent_id, *, release_terminal=True) -> AgentRunSnapshot`
- `respond_to_request(...) -> AgentInstanceSnapshot`

### `runs`

Read methods:

- `get(run_id) -> AgentRunSnapshot | None`
- `list(*, agent_id=None, task_id=None, role=None) -> list[AgentRunSnapshot]`
- `for_task(task_id, *, role=None) -> list[AgentRunSnapshot]`
- `for_instance(agent_id) -> list[AgentRunSnapshot]`
- `latest_for_instance(agent_id) -> AgentRunSnapshot | None`
- `latest_for_task(task_id, *, role=None) -> AgentRunSnapshot | None`
- `events(run_id) -> list[CanonicalEvent]`
- `subscribe(run_id, handler, *, event_types=None) -> Callable[[], None]`

`events(run_id)` replays the run's canonical event log in order.
`subscribe(...)` is a live, non-durable hook for future canonical events only.

### `documents`

Read/write methods:

- `snapshot() -> DocumentSnapshot`
- `roadmap() -> RoadmapDocument | None`
- `consensus() -> ConsensusDocument | None`
- `consensus_source_path() -> Path | None`
- `update_consensus(...) -> ConsensusDocument`
- `write_consensus(document) -> ConsensusDocument`
- `replace_roadmap(...) -> RoadmapDocument`

### `tasks`

Read/control methods:

- `get(task_id) -> TaskInfo | None`
- `list() -> list[TaskInfo]`
- `add(task, *, index=None) -> TaskInfo`
- `update(task_id, **updates) -> TaskInfo`
- `reorder(ordered_task_ids) -> RoadmapDocument`
- `summaries() -> dict[str, str]`
- `review(task_id, *, decision, failure_reason=None) -> TaskInfo`
- `queue_retry(task_id, *, failure_reason, prompt=None, acceptance_criteria=None) -> TaskInfo`

### `questions`

Read/control methods:

- `get(question_id) -> QuestionRecord | None`
- `list() -> list[QuestionRecord]`
- `pending() -> list[QuestionRecord]`
- `current() -> QuestionRecord | None`
- `ask(...) -> QuestionRecord`
- `answer(answer, *, question_id=None) -> QuestionAnswerResult`
- `resolve(question_id, *, answer=None) -> QuestionRecord`
- `sync_pending(...) -> list[QuestionRecord]`

## Compatibility Aliases

The facade still exposes the older flat helpers so current first-party users do
not need to migrate in the same change. These names are compatibility aliases,
not the canonical contract:

- `get_workflow_status()`
- `get_consensus_document()`
- `get_roadmap()`
- `get_consensus_source_path()`
- `get_task()`
- `add_task()`
- `update_task()`
- `reorder_tasks()`
- `replace_roadmap()`
- `update_consensus()`
- `ask_question()`
- `request_user_decision()`
- `set_pending_questions()`
- `resolve_question()`
- `get_task_summaries()`
- `submit_gatekeeper_message()`
- `answer_pending_question()`
- `execute_next_task()`
- `execute_until_blocked()`
- `pause_workflow()`
- `resume_workflow()`
- `end_planning_phase()`
- `review_task_outcome()`
- `mark_task_for_retry()`
- `list_pending_questions()`
- `get_current_pending_question()`

The two async compatibility Gatekeeper helpers intentionally keep their older
return values for now:

- `submit_gatekeeper_message()` returns the raw Gatekeeper run result
- `answer_pending_question()` returns the raw Gatekeeper run result

## Non-Stable Internals

The following should not be treated as stable external contracts even if they
are importable today:

- `AgentRunRecord`
- internal orchestrator services
- state backend internals
- persistence file layout
- raw provider/runtime record schemas
