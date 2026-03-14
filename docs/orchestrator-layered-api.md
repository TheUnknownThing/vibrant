# Proposed Orchestrator Layered API

This document proposes the next stable orchestrator API after the current
**role / agent-instance / run** architecture described in
`docs/agent-role-architecture.md` and the behavioral boundary described in
`docs/phase-2-role-results.md`.

It is a design proposal, not a description of the current implementation.

## Goal

Redesign the stable API so it matches the real system layers:

- **Role** = policy and capabilities
- **Agent instance** = stable logical actor identity
- **Run** = one execution of that actor
- **Orchestrator workflow** = durable decisions and side effects

The public API should expose those layers directly instead of flattening them
into a mixed set of `agent_*`, `get_*`, and task-lifecycle compatibility
helpers.

## Problems With The Current Surface

The current stable API has three main issues:

1. It mixes stable actor state and run state in one snapshot type.
2. It exposes compatibility names and legacy result shapes as if they were the
   long-term contract.
3. It surfaces role meaning inconsistently, even though `role_result` already
   exists in runtime, persistence, and projections.

Examples of current drift:

- the documented facade names in `vibrant/orchestrator/STABLE_API.md` do not
  fully match the methods implemented in `vibrant/orchestrator/facade.py`
- `OrchestratorAgentSnapshot` combines instance identity with latest-run data
- `TaskResult` still carries raw runtime details that should not define the
  long-term stable contract
- `AgentRunRecord` is still exposed directly through the facade, even though it
  is an internal persistence model

## Design Principles

The redesigned stable API should follow these rules:

- expose **role**, **instance**, and **run** as first-class nouns
- keep orchestrator-owned actions under workflow, task, question, and document
  namespaces
- make **run envelope** and **role payload** explicit and separate
- expose stable read models, not raw persistence records
- keep backward-compatibility aliases temporarily, but stop treating them as
  canonical

## Proposed Public Root

Keep `OrchestratorFacade` as the stable entry point, but make it a namespace
root instead of a flat helper bag.

```python
class OrchestratorFacade:
    roles: RoleAPI
    instances: InstanceAPI
    runs: RunAPI
    documents: DocumentAPI
    tasks: TaskAPI
    questions: QuestionAPI
    workflow: WorkflowAPI

    def snapshot(self) -> OrchestratorSnapshot: ...
```

This keeps the root small while making the domain boundaries explicit.

`gatekeeper` may still exist as an optional convenience alias for starting a
new Gatekeeper conversation turn, but it is not a core architectural layer and
should not duplicate the general instance/run APIs.

## Stable Read Models

### `OrchestratorSnapshot`

The top-level snapshot should become an aggregation of explicit layer snapshots:

```python
@dataclass(frozen=True)
class OrchestratorSnapshot:
    workflow: WorkflowSnapshot
    documents: DocumentSnapshot
    questions: tuple[QuestionRecord, ...]
    roles: tuple[RoleSnapshot, ...]
    instances: tuple[AgentInstanceSnapshot, ...]
```

This is the main read model for TUI and MCP consumers that want one coherent
view of orchestrator state.

### `WorkflowSnapshot`

```python
@dataclass(frozen=True)
class WorkflowSnapshot:
    status: OrchestratorStatus
    execution_mode: RoadmapExecutionMode | None
    user_input_banner: str
    notification_bell_enabled: bool
```

### `DocumentSnapshot`

```python
@dataclass(frozen=True)
class DocumentSnapshot:
    roadmap: RoadmapDocument | None
    consensus: ConsensusDocument | None
    consensus_path: Path | None
```

### `RoleSnapshot`

This is the stable public projection of role policy from the role catalog.

```python
@dataclass(frozen=True)
class RoleSnapshot:
    role: str
    display_name: str
    workflow_class: str
    default_provider_kind: str
    default_runtime_mode: str
    supports_interactive_requests: bool
    persistent_thread: bool
    ui_model_name: str | None = None
```

### `AgentInstanceSnapshot`

This replaces the current overloaded `OrchestratorAgentSnapshot` as the primary
stable actor view.

```python
@dataclass(frozen=True)
class AgentInstanceSnapshot:
    agent_id: str
    role: str
    scope_type: str
    scope_id: str | None
    provider_defaults: ProviderDefaultsSnapshot
    supports_interactive_requests: bool
    persistent_thread: bool
    latest_run_id: str | None
    active_run_id: str | None
    active: bool
    awaiting_input: bool
    latest_run: AgentRunRef | None
```

The instance owns stable identity and stable provider defaults. It does not own
full run details.

### `AgentRunRef`

This is the light summary embedded inside an instance snapshot.

```python
@dataclass(frozen=True)
class AgentRunRef:
    run_id: str
    task_id: str | None
    lifecycle_status: str
    runtime_state: str
    summary: str | None
    error: str | None
    awaiting_input: bool
    started_at: datetime | None
    finished_at: datetime | None
```

### `AgentRunSnapshot`

This is the stable read model for one execution.

```python
@dataclass(frozen=True)
class AgentRunSnapshot:
    run_id: str
    agent_id: str
    task_id: str | None
    role: str
    lifecycle: RunLifecycleSnapshot
    runtime: RunRuntimeSnapshot
    workspace: RunWorkspaceSnapshot
    provider: RunProviderSnapshot
    envelope: RunEnvelope
    payload: RolePayload | None
```

The important boundary is:

- `envelope` = generic runtime facts
- `payload` = role-specific meaning

The stable API should not use `AgentRunRecord` as the public run type.

### `QuestionRecord`

Questions are orchestrator-owned artifacts. They may be created from a
Gatekeeper run result, but they are not themselves provider/runtime requests.

To support traceability, the stable question model should carry run linkage:

```python
@dataclass(frozen=True)
class QuestionRecord:
    question_id: str
    source_agent_id: str | None
    source_role: str
    source_run_id: str | None
    text: str
    priority: QuestionPriority
    status: QuestionStatus
    answer: str | None
    resolved_by_run_id: str | None
    created_at: datetime
    resolved_at: datetime | None
```

This makes the relationship explicit:

- a Gatekeeper run may create a question record
- answering a question may trigger a later Gatekeeper run
- the question record remains orchestrator-owned state

## Run Envelope vs Role Payload

The shared runtime envelope should contain only host/runtime facts.

```python
@dataclass(frozen=True)
class RunEnvelope:
    state: str
    summary: str | None
    error: str | None
    input_requests: tuple[InputRequest, ...]
    canonical_event_log: str | None
    native_event_log: str | None
    provider_thread_id: str | None
    provider_thread_path: str | None
    resume_cursor: dict[str, Any] | None
```

The role payload should contain role meaning, not workflow decisions.

```python
class RolePayload(BaseModel):
    role: str
    semantic_status: str
```

Built-in payloads should become:

- `CodeRunPayload`
- `ValidationRunPayload`
- `MergeRunPayload`
- `GatekeeperDecisionPayload`

Examples of semantic status values:

- code: `completed`, `blocked`, `needs_input`
- validation: `passed`, `failed`, `needs_input`
- merge: `resolved`, `blocked`, `needs_input`
- gatekeeper: `accepted`, `retry_requested`, `needs_user_decision`,
  `replan_requested`, `escalated`

The payload should not decide whether the orchestrator retries, pauses,
requeues, merges, or updates workflow state. That remains orchestrator-owned.

## Proposed Facade Namespaces

### `workflow`

The workflow namespace owns global workflow control and task execution.

```python
facade.workflow.status() -> OrchestratorStatus
facade.workflow.pause() -> None
facade.workflow.resume() -> None
facade.workflow.end_planning() -> OrchestratorStatus
facade.workflow.execute_next_task() -> TaskExecutionResult | None
facade.workflow.execute_until_blocked() -> list[TaskExecutionResult]
```

### `roles`

The role namespace exposes role policy and capabilities.

```python
facade.roles.get(role: str) -> RoleSnapshot | None
facade.roles.list() -> list[RoleSnapshot]
```

### `instances`

The instance namespace exposes stable logical actors.

```python
facade.instances.get(agent_id: str) -> AgentInstanceSnapshot | None
facade.instances.list(
    *,
    task_id: str | None = None,
    role: str | None = None,
    include_completed: bool = True,
    active_only: bool = False,
) -> list[AgentInstanceSnapshot]
facade.instances.active() -> list[AgentInstanceSnapshot]
facade.instances.wait(agent_id: str, *, release_terminal: bool = True) -> AgentRunSnapshot
facade.instances.respond_to_request(
    agent_id: str,
    request_id: str | int,
    *,
    result: object | None = None,
    error: dict[str, object] | None = None,
) -> AgentInstanceSnapshot
```

### `runs`

The run namespace exposes execution history directly.

```python
facade.runs.get(run_id: str) -> AgentRunSnapshot | None
facade.runs.list(
    *,
    agent_id: str | None = None,
    task_id: str | None = None,
    role: str | None = None,
) -> list[AgentRunSnapshot]
facade.runs.for_task(task_id: str, *, role: str | None = None) -> list[AgentRunSnapshot]
facade.runs.for_instance(agent_id: str) -> list[AgentRunSnapshot]
facade.runs.latest_for_instance(agent_id: str) -> AgentRunSnapshot | None
facade.runs.latest_for_task(task_id: str, *, role: str | None = None) -> AgentRunSnapshot | None
facade.runs.events(run_id: str) -> list[CanonicalEvent]
facade.runs.subscribe(
    run_id: str,
    handler: Callable[[CanonicalEvent], Awaitable[None] | None],
    *,
    event_types: list[str] | tuple[str, ...] | set[str] | None = None,
) -> Callable[[], None]
```

`facade.runs.events(...)` replays the run's canonical event log in order.
`facade.runs.subscribe(...)` observes future canonical events only and is not durable.

The Gatekeeper should be accessed through the same generic APIs:

```python
gatekeeper = facade.instances.get("gatekeeper-project")
latest_gatekeeper_run = facade.runs.latest_for_instance("gatekeeper-project")
gatekeeper_history = facade.runs.for_instance("gatekeeper-project")
```

### `documents`

The document namespace owns orchestrator-managed durable documents.

```python
facade.documents.roadmap() -> RoadmapDocument | None
facade.documents.consensus() -> ConsensusDocument | None
facade.documents.consensus_source_path() -> Path | None
facade.documents.update_consensus(...) -> ConsensusDocument
facade.documents.replace_roadmap(...) -> RoadmapDocument
```

### `tasks`

The task namespace owns roadmap task reads and orchestrator task actions.

```python
facade.tasks.get(task_id: str) -> TaskInfo | None
facade.tasks.list() -> list[TaskInfo]
facade.tasks.add(task: TaskInfo, *, index: int | None = None) -> TaskInfo
facade.tasks.update(task_id: str, **updates) -> TaskInfo
facade.tasks.reorder(ordered_task_ids: list[str]) -> RoadmapDocument
facade.tasks.summaries() -> dict[str, str]
facade.tasks.review(
    task_id: str,
    *,
    decision: str,
    failure_reason: str | None = None,
) -> TaskInfo
facade.tasks.queue_retry(
    task_id: str,
    *,
    failure_reason: str,
    prompt: str | None = None,
    acceptance_criteria: Sequence[str] | None = None,
) -> TaskInfo
```

### `questions`

The question namespace owns user-facing structured questions.

```python
facade.questions.list() -> list[QuestionRecord]
facade.questions.pending() -> list[QuestionRecord]
facade.questions.current() -> QuestionRecord | None
facade.questions.ask(...) -> QuestionRecord
facade.questions.answer(
    answer: str,
    *,
    question_id: str | None = None,
) -> QuestionAnswerResult
facade.questions.resolve(question_id: str, *, answer: str | None = None) -> QuestionRecord
facade.questions.sync_pending(...) -> list[QuestionRecord]
```

Questions are the user-facing durable artifact. The orchestrator may create them
from a Gatekeeper run result, but answering them is an orchestrator-owned flow,
not a provider-request response on an old completed run.

Answering a question should start a new Gatekeeper run on the stable
`gatekeeper-project` instance and return both the updated question record and
that new run:

```python
@dataclass(frozen=True)
class QuestionAnswerResult:
    question: QuestionRecord
    gatekeeper_run: AgentRunSnapshot
```

### Optional `gatekeeper` Convenience Alias

The API may expose a thin convenience alias:

```python
await facade.gatekeeper.submit(text: str) -> AgentRunSnapshot
```

This exists only to express the product-level intent "start a new unsolicited
Gatekeeper conversation turn."

It should not:

- expose Gatekeeper status or history
- duplicate `instances.get(...)`
- duplicate `runs.get(...)` or `runs.for_instance(...)`
- answer pending questions
- own roadmap, consensus, workflow, or task actions

If that convenience alias becomes broader than a simple submit operation, it is
a design smell and should be removed.

## Proposed Stable Execution Result

The current task execution result should be replaced by a more intentional
stable type.

```python
@dataclass(frozen=True)
class TaskExecutionResult:
    task_id: str | None
    task_status: TaskStatus | None
    workflow_outcome: str
    agent_id: str | None
    run_id: str | None
    summary: str | None
    error: str | None
    payload: RolePayload | None
```

This is the correct boundary:

- `payload` = what the role meant
- `workflow_outcome` = what the orchestrator decided to do

The stable type should not expose raw `AgentRunRecord`, `GatekeeperRunResult`,
`GitMergeResult`, raw event lists, or worktree paths as first-class contract
fields.

## Canonical Public Names

The stable API should stop using `agent` as the main public noun.

Canonical nouns:

- `role`
- `instance`
- `run`
- `workflow`
- `task`
- `question`
- `document`

Compatibility aliases may exist temporarily, but they should be clearly marked
as transitional.

## Deprecated Compatibility Mapping

The following current names should become compatibility aliases only:

- `get_workflow_status()` -> `workflow.status()`
- `get_consensus_document()` -> `documents.consensus()`
- `get_roadmap()` -> `documents.roadmap()`
- `get_consensus_source_path()` -> `documents.consensus_source_path()`
- `get_task()` -> `tasks.get()`
- `get_task_summaries()` -> `tasks.summaries()`
- `list_question_records()` -> `questions.list()`
- `list_pending_question_records()` -> `questions.pending()`
- `list_pending_questions()` -> text-only question projection if still needed
- `get_current_pending_question()` -> text-only projection of `questions.current()`
- `get_agent()` -> `instances.get()`
- `get_agent_instance()` -> `instances.get()`
- `list_agents()` -> `instances.list()`
- `list_agent_instances()` -> `instances.list()`
- `list_active_agents()` -> `instances.active()`
- `list_agent_records()` -> `runs.list()`
- `list_agent_run_records()` -> `runs.list()`
- `submit_gatekeeper_message()` -> optional `gatekeeper.submit()` convenience alias
- `answer_pending_question()` -> `questions.answer()`
- `TaskResult` -> `TaskExecutionResult`
- `OrchestratorAgentSnapshot` -> `AgentInstanceSnapshot`
- `role_result` -> `payload`

## Stable Export Set

The proposed stable package-level exports are:

- `OrchestratorFacade`
- `OrchestratorSnapshot`
- `WorkflowSnapshot`
- `DocumentSnapshot`
- `RoleSnapshot`
- `AgentInstanceSnapshot`
- `AgentRunSnapshot`
- `TaskExecutionResult`
- `OrchestratorMCPServer`

The following should no longer be treated as stable public contract types:

- `AgentRunRecord`
- `TaskResult`
- `GatekeeperRunResult`
- `GitMergeResult`
- internal orchestrator service classes

## MCP Alignment

The MCP surface should mirror the same layered nouns.

Preferred read tools/resources:

- `role_get`
- `role_list`
- `instance_get`
- `instance_list`
- `run_get`
- `run_list`
- `task_get`
- `workflow_status`
- `questions_pending`

Preferred action tools:

- `workflow_execute_next_task`
- `workflow_pause`
- `workflow_resume`
- `question_answer`
- `task_review`
- `task_queue_retry`
- `document_update_consensus`
- `document_replace_roadmap`

`gatekeeper_submit` may still exist as a convenience alias. Existing `agent_*`
and `vibrant.*` MCP names can remain as aliases during migration, but they
should stop being the canonical names in documentation.

## Example Usage

```python
facade = OrchestratorFacade(orchestrator)

workflow = facade.workflow.status()
instances = facade.instances.list(active_only=True)
runs = facade.runs.for_task("task-001")
task = facade.tasks.get("task-001")
question = facade.questions.current()
gatekeeper = facade.instances.get("gatekeeper-project")

result = await facade.workflow.execute_next_task()
if result is not None and result.workflow_outcome == "accepted":
    print(result.task_id, result.run_id)
```

## Migration Plan

### Phase 1: Add Canonical Namespaces

Add the namespace objects to the facade while keeping the current flat helper
methods as aliases.

### Phase 2: Split Instance And Run Models

Introduce `AgentInstanceSnapshot` and `AgentRunSnapshot`, while leaving
`OrchestratorAgentSnapshot` as a deprecated compatibility projection.

### Phase 3: Replace The Stable Execution Result

Introduce `TaskExecutionResult` and alias `TaskResult` to it for one migration
cycle.

### Phase 4: Promote Payloads Publicly

Rename the stable public field from `role_result` to `payload` and document the
built-in role payload types explicitly.

### Phase 5: Align MCP And TUI

Move first-party consumers to the canonical names and the new stable models.

### Phase 6: Remove Compatibility Names

After TUI, MCP, and tests are migrated, remove the compatibility names from the
documented stable contract.

## Non-Goals

This redesign does not:

- move workflow decision authority into roles
- make raw provider-native objects part of the stable API
- expose persistence records directly as long-term public models
- make Gatekeeper a separate architectural layer alongside role, instance, and run
- require the orchestrator internals to mirror the public namespace structure
  exactly

## Final Position

The stable API should reflect the architecture that now exists:

- roles define policy and typed meaning
- instances define stable actor identity
- runs define individual executions
- the orchestrator defines durable actions and workflow consequences

That is the API shape that best fits the new layers.
