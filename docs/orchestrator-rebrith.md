# Orchestrator Redesign Proposal

## 1. Design Rules

The redesign is based on five non-negotiable rules.

- The orchestrator owns all durable state under `.vibrant/`.
- The orchestrator owns the lifecycle of every agent, including the Gatekeeper.
- The Gatekeeper never mutates orchestrator state by writing files or by prose output.
- The Gatekeeper mutates orchestrator state only through typed MCP tools.
- The orchestrator never infers planning or review decisions from `GatekeeperRunResult`, roadmap diffs, or free-form text.

This changes the architecture from a result-parsing model to a command-driven control-plane model.

## 2. Authority Model

### Orchestrator authority

The orchestrator is the authority for:

- Gatekeeper lifecycle
- worker lifecycle
- workflow status
- task state transitions
- roadmap persistence
- consensus persistence
- question persistence
- review state persistence
- event logging
- recovery after restart

### Gatekeeper authority

The Gatekeeper is the authority for:

- planning decisions
- roadmap edits
- consensus edits
- user-question requests
- task review decisions
- pause/resume decisions

But it expresses those decisions only through MCP commands issued to the orchestrator.

### Worker authority

Workers are not control-plane authorities.

They may:

- read task, roadmap, and consensus context
- later report progress or blockers through typed tools

They may not:

- mutate roadmap structure
- mutate consensus
- control workflow state
- control the Gatekeeper lifecycle

## 3. Top-Level Subsystems

### 3.1 Control Plane

Purpose: the only cross-subsystem coordinator.

Responsibilities:

- own the global orchestrator state machine
- decide when Gatekeeper must start, resume, restart, or stop
- route user chat and user answers
- start execution
- coordinate dispatch, review waiting, merge handling, and completion
- publish domain events for TUI and MCP resources

It must not parse markdown, manage worktrees directly, or speak provider-specific runtime protocols.

Primary interface:

```python
class OrchestratorControlPlane:
    async def submit_user_message(self, text: str) -> GatekeeperTurnHandle: ...
    async def answer_user_decision(self, question_id: str, answer: str) -> GatekeeperTurnHandle: ...
    async def start_execution(self) -> WorkflowSnapshot: ...
    async def pause_workflow(self) -> WorkflowSnapshot: ...
    async def resume_workflow(self) -> WorkflowSnapshot: ...
    async def restart_gatekeeper(self, reason: str | None = None) -> GatekeeperSessionSnapshot: ...
    async def stop_gatekeeper(self) -> GatekeeperSessionSnapshot: ...
    def snapshot(self) -> OrchestratorSnapshot: ...
```

### 3.2 Gatekeeper Lifecycle

Purpose: own Gatekeeper runtime lifecycle only.

Responsibilities:

- spawn the Gatekeeper
- resume the Gatekeeper from persisted provider thread metadata
- attach Gatekeeper MCP capability scope
- track lifecycle state and health
- interrupt, stop, and restart the Gatekeeper
- send messages into the active Gatekeeper session
- return runtime-level turn results only

It must not:

- write roadmap or consensus
- apply question changes
- apply workflow transitions
- interpret free-form Gatekeeper text

Primary interface:

```python
class GatekeeperLifecycleService:
    async def ensure_started(self) -> GatekeeperSessionSnapshot: ...
    async def resume_or_start(self) -> GatekeeperSessionSnapshot: ...
    async def send_message(
        self,
        *,
        kind: GatekeeperMessageKind,
        text: str,
        resume: bool = True,
    ) -> GatekeeperTurnHandle: ...
    async def interrupt(self) -> GatekeeperSessionSnapshot: ...
    async def stop(self) -> GatekeeperSessionSnapshot: ...
    async def restart(self, *, reason: str | None = None) -> GatekeeperSessionSnapshot: ...
    def snapshot(self) -> GatekeeperSessionSnapshot: ...
    def is_available(self) -> bool: ...
```

Required types:

```python
@dataclass
class GatekeeperSessionSnapshot:
    agent_id: str | None
    lifecycle_state: Literal[
        "not_started", "starting", "running", "awaiting_user",
        "idle", "failed", "stopped"
    ]
    provider_thread_id: str | None
    resumable: bool
    last_error: str | None
    active_turn: bool

@dataclass
class GatekeeperTurnHandle:
    session: GatekeeperSessionSnapshot
    turn_id: str
    done: bool
    async def wait(self) -> GatekeeperTurnResult: ...

@dataclass
class GatekeeperTurnResult:
    session: GatekeeperSessionSnapshot
    summary_text: str | None
    input_requests: list[InputRequest]
    runtime_error: str | None
```

Decision:

- `GatekeeperTurnResult` is runtime/session output only.
- It is not a state-mutation payload.

### 3.3 MCP Control Surface

Purpose: the authoritative control protocol used by the Gatekeeper.

Responsibilities:

- expose read resources and write tools
- enforce Gatekeeper-only permissions
- validate all arguments
- translate MCP calls into semantic command handlers

For the Gatekeeper, MCP is not a convenience layer. It is the mutation path.

Required read resources:

```python
get_consensus() -> ConsensusView
get_roadmap() -> RoadmapView
get_task(task_id: str) -> TaskView
get_review_ticket(task_id: str) -> ReviewTicketView | None
get_workflow_status() -> WorkflowStatusView
list_pending_questions() -> list[QuestionView]
list_active_agents() -> list[AgentRuntimeView]
list_recent_events(limit: int = 20) -> list[DomainEventView]
```

Required write tools:

```python
update_consensus(...) -> ConsensusView
add_task(...) -> TaskView
update_task_definition(...) -> TaskView
reorder_tasks(task_ids: list[str]) -> RoadmapView
request_user_decision(...) -> QuestionView
resolve_question(question_id: str, answer: str | None = None) -> QuestionView
end_planning_phase() -> WorkflowStatusView
pause_workflow() -> WorkflowStatusView
resume_workflow() -> WorkflowStatusView
record_task_accept(task_id: str) -> TaskReviewOutcomeView
record_task_retry(
    task_id: str,
    failure_reason: str,
    prompt_patch: str | None = None,
    acceptance_patch: Sequence[str] | None = None,
) -> TaskReviewOutcomeView
record_task_escalation(task_id: str, reason: str) -> TaskReviewOutcomeView
```

Decision:

- Do not keep `review_task_outcome(decision=...)`.
- Use explicit commands instead.

### 3.4 Workflow Policy

Purpose: own all execution policy and the task state machine.

Responsibilities:

- dependency scheduling
- dispatch eligibility
- concurrency rules
- retry and escalation policy
- review-required transitions
- workflow completion detection
- blocking rules for pending questions and Gatekeeper failure

Required task states:

- `pending`
- `ready`
- `leased`
- `running`
- `review_pending`
- `accepted`
- `retry_pending`
- `escalated`

Decision:

- Remove the overloaded meaning of `completed`.
- Worker completion and Gatekeeper acceptance are different states and must remain different.

Primary interface:

```python
class WorkflowPolicyService:
    def load_snapshot(self) -> WorkflowSnapshot: ...
    def select_next(self, *, limit: int) -> list[DispatchLease]: ...
    def on_attempt_started(self, lease: DispatchLease, attempt: RunningAttempt) -> WorkflowSnapshot: ...
    def on_attempt_finished(self, outcome: TaskRunOutcome) -> WorkflowSnapshot: ...
    def apply_review_decision(self, decision: TaskReviewDecision) -> WorkflowSnapshot: ...
    def maybe_complete(self) -> WorkflowSnapshot: ...
```

Required types:

```python
@dataclass
class DispatchLease:
    task_id: str
    lease_id: str
    branch_hint: str | None

@dataclass
class TaskRunOutcome:
    task_id: str
    attempt_id: str
    agent_id: str
    status: Literal["succeeded", "failed", "awaiting_input", "cancelled"]
    summary: str | None
    error: str | None
    workspace_ref: str | None
    provider_events_ref: str | None

@dataclass
class TaskReviewDecision:
    task_id: str
    decision: Literal["accept", "retry", "escalate"]
    failure_reason: str | None
    prompt_patch: str | None
    acceptance_patch: list[str] | None
```

### 3.5 Review Control

Purpose: own asynchronous review coordination between finished task attempts and Gatekeeper decisions.

Responsibilities:

- create a review ticket when a task attempt ends
- persist the review context durably
- expose review tickets through MCP resources
- accept explicit review commands from Gatekeeper
- coordinate merge on accept
- create a follow-up review ticket if merge fails

Primary interface:

```python
class ReviewControlService:
    def create_ticket(self, outcome: TaskRunOutcome, workspace: WorkspaceHandle, diff: DiffArtifact | None) -> ReviewTicket: ...
    def get_ticket(self, task_id: str) -> ReviewTicket | None: ...
    def list_pending(self) -> list[ReviewTicket]: ...
    def record_accept(self, task_id: str) -> TaskReviewOutcomeView: ...
    def record_retry(
        self,
        task_id: str,
        failure_reason: str,
        prompt_patch: str | None = None,
        acceptance_patch: Sequence[str] | None = None,
    ) -> TaskReviewOutcomeView: ...
    def record_escalation(self, task_id: str, reason: str) -> TaskReviewOutcomeView: ...
```

Decision:

- Review is asynchronous and command-driven.
- The execution pipeline does not wait for a parsed Gatekeeper verdict.
- The system waits for an explicit MCP review command.

### 3.6 Execution Coordinator

Purpose: run worker attempts mechanically.

Responsibilities:

- prepare workspace
- assemble task prompt and injected context
- create agent record
- start runtime
- wait for runtime result
- return `TaskRunOutcome`

It must not:

- decide retry or escalation
- call Gatekeeper directly
- mark tasks accepted
- complete workflow

Primary interface:

```python
class ExecutionCoordinator:
    async def start_attempt(self, lease: DispatchLease) -> RunningAttempt: ...
    async def await_attempt(self, attempt_id: str) -> TaskRunOutcome: ...
```

Required type:

```python
@dataclass
class RunningAttempt:
    attempt_id: str
    task_id: str
    agent_id: str
    workspace: WorkspaceHandle
```

### 3.7 Runtime

Purpose: generic provider/runtime mechanism shared by Gatekeeper and workers.

Responsibilities:

- start, resume, wait, interrupt, kill agent runs
- track live handles
- resolve provider thread metadata
- provide runtime snapshots

This subsystem is already close to the right boundary.

Stable interface:

```python
class AgentRuntimeService:
    async def start_run(...) -> AgentHandle: ...
    async def resume_run(...) -> AgentHandle: ...
    async def wait_for_run(...) -> RuntimeExecutionResult: ...
    async def interrupt_run(...) -> RuntimeHandleSnapshot: ...
    async def kill_run(...) -> RuntimeHandleSnapshot: ...
    def snapshot_handle(...) -> RuntimeHandleSnapshot: ...
```

### 3.8 Workspace

Purpose: own worktree and merge mechanics.

Responsibilities:

- create worktrees
- map branch names
- collect diffs
- merge accepted work
- abort merge when needed
- clean up worktrees

Primary interface:

```python
class WorkspaceService:
    def prepare_task_workspace(self, task_id: str, branch_hint: str | None = None) -> WorkspaceHandle: ...
    def collect_review_diff(self, task_id: str, workspace: WorkspaceHandle) -> DiffArtifact: ...
    def merge_task_result(self, task_id: str, workspace: WorkspaceHandle) -> MergeOutcome: ...
    def cleanup_workspace(self, task_id: str) -> None: ...
```

## 4. Durable Stores

### 4.1 WorkflowStateStore

Backing file: `.vibrant/state.json`

Persist only non-derivable workflow/session facts:

- `session_id`
- `started_at`
- `workflow_status`
- `concurrency_limit`
- `gatekeeper_lifecycle_status`
- `gatekeeper_active_agent_id`
- `current_review_task_id` or equivalent review pointer if needed
- `total_agent_spawns`

Do not persist:

- `active_agents`
- `completed_tasks`
- `failed_tasks`
- `provider_runtime`
- `pending_questions`
- `last_consensus_version`

Interface:

```python
class WorkflowStateStore:
    def load(self) -> WorkflowState: ...
    def save(self, state: WorkflowState) -> None: ...
    def set_workflow_status(self, status: WorkflowStatus) -> WorkflowState: ...
    def set_gatekeeper_lifecycle(self, status: GatekeeperLifecycleStatus, agent_id: str | None = None) -> WorkflowState: ...
    def set_concurrency_limit(self, limit: int) -> WorkflowState: ...
```

### 4.2 QuestionStore

Backing file: `.vibrant/questions.json`

Decision:

- Questions move out of `state.json`.
- Stable IDs are mandatory.
- Text reconciliation is removed as an authority mechanism.

Interface:

```python
class QuestionStore:
    def list(self) -> list[QuestionRecord]: ...
    def list_pending(self) -> list[QuestionRecord]: ...
    def get(self, question_id: str) -> QuestionRecord | None: ...
    def create(self, question: CreateQuestion) -> QuestionRecord: ...
    def resolve(self, question_id: str, answer: str | None = None) -> QuestionRecord: ...
```

### 4.3 ConsensusStore

Backing file: `.vibrant/consensus.md`

Decision:

- The file remains human-readable.
- The Gatekeeper does not write it directly.
- The orchestrator writes it in response to MCP commands.

Interface:

```python
class ConsensusStore:
    def load(self) -> ConsensusDocument | None: ...
    def write(self, document: ConsensusDocument) -> ConsensusDocument: ...
    def update_context(self, context: str) -> ConsensusDocument: ...
    def append_decision(...) -> ConsensusDocument: ...
    def set_status(self, status: ConsensusStatus) -> ConsensusDocument: ...
```

### 4.4 RoadmapStore

Backing file: `.vibrant/roadmap.md`

Decision:

- Remove scheduler ownership from roadmap persistence.
- Do not expose generic task status patching to outside callers.

Interface:

```python
class RoadmapStore:
    def load(self) -> RoadmapDocument: ...
    def write(self, document: RoadmapDocument) -> RoadmapDocument: ...
    def get_task(self, task_id: str) -> TaskInfo | None: ...
    def add_task(self, task: TaskInfo, index: int | None = None) -> RoadmapDocument: ...
    def update_task_definition(self, task_id: str, patch: TaskDefinitionPatch) -> TaskInfo: ...
    def apply_transition(self, task_id: str, transition: TaskTransition, meta: TaskTransitionMeta | None = None) -> TaskInfo: ...
    def reorder_tasks(self, task_ids: list[str]) -> RoadmapDocument: ...
```

### 4.5 ReviewTicketStore

Backing file: `.vibrant/reviews.json`

Purpose:

- persist pending review items across restart
- record the current review context for each task attempt

Interface:

```python
class ReviewTicketStore:
    def create(self, ticket: CreateReviewTicket) -> ReviewTicket: ...
    def get(self, ticket_id: str) -> ReviewTicket | None: ...
    def get_by_task(self, task_id: str) -> ReviewTicket | None: ...
    def list_pending(self) -> list[ReviewTicket]: ...
    def resolve(self, ticket_id: str, resolution: ReviewResolution) -> ReviewTicket: ...
```

### 4.6 AgentRecordStore

Backing files: `.vibrant/agents/*.json`

Decision:

- Keep it as the source of truth for durable per-agent records.
- Remove automatic global state rebuilds during upsert.

Interface:

```python
class AgentRecordStore:
    def get(self, agent_id: str) -> AgentRecord | None: ...
    def list(self) -> list[AgentRecord]: ...
    def list_by_task(self, task_id: str) -> list[AgentRecord]: ...
    def upsert(self, record: AgentRecord, increment_spawn: bool = False) -> Path: ...
    def provider_thread(self, agent_id: str) -> ProviderThreadHandle | None: ...
```

### 4.7 EventLogStore

Backing file: `.vibrant/logs/orchestrator.ndjson`

Purpose:

- record command and domain events for debugging and recovery

Interface:

```python
class EventLogStore:
    def append_domain_event(self, event: DomainEvent) -> None: ...
    def list_recent(self, limit: int) -> list[DomainEvent]: ...
```

## 5. Read Models

These are query-only projections. TUI and MCP resources must read from the same projections.

- `WorkflowSnapshotReadModel`
  - workflow status
  - gatekeeper lifecycle status
  - blocking reason
  - concurrency limit

- `QuestionQueueReadModel`
  - pending and resolved questions with stable IDs

- `AgentRuntimeReadModel`
  - active agents
  - runtime state
  - resumable provider thread data
  - summaries and errors

- `RoadmapExecutionReadModel`
  - roadmap
  - ready tasks
  - blocked tasks
  - dependency state
  - review-pending tasks

- `ConsensusSnapshotReadModel`
  - parsed consensus plus metadata

Decision:

- Replace durable projection fields like `state.active_agents` and `state.provider_runtime` with read-model computation.

## 6. Compatibility Rules Between Subsystems

These rules make the interfaces compatible and prevent the current cross-layer leakage.

- Control Plane may orchestrate all major subsystems, but no other subsystem may coordinate the whole workflow.
- Gatekeeper Lifecycle may depend on Runtime and AgentRecordStore, but not on RoadmapStore, ConsensusStore, QuestionStore, or WorkflowPolicy.
- MCP write handlers may call only semantic command services. They may not patch store internals directly.
- Workflow Policy may persist task transitions through `RoadmapStore.apply_transition`, but it may not call Runtime directly.
- Execution Coordinator may use Runtime, Workspace, AgentRecordStore, and prompt builders, but it may not apply retry, escalation, or acceptance decisions.
- Review Control may use ReviewTicketStore, Workspace, and WorkflowPolicy, but it may not infer decisions from text.
- Read Models may depend on stores and runtime snapshots, but they may not mutate anything.
- RoadmapStore may own task definitions and persisted task transitions, but it may not own scheduler state.

## 7. End-to-End Flows

### 7.1 Planning flow

1. Control Plane ensures the Gatekeeper is running.
2. User message is sent through Gatekeeper Lifecycle.
3. Gatekeeper reads roadmap, consensus, workflow, and questions through MCP.
4. Gatekeeper issues typed MCP commands to update roadmap, consensus, and questions.
5. Stores persist each change immediately.
6. Read Models update.
7. Gatekeeper text remains informational only.

### 7.2 Execution flow

1. Workflow Policy selects ready tasks and issues `DispatchLease`.
2. Execution Coordinator creates a worker attempt and waits for completion.
3. A `TaskRunOutcome` is produced.
4. Workflow Policy transitions the task to `review_pending`.
5. Review Control creates a review ticket.
6. Gatekeeper reads the review ticket through MCP.
7. Gatekeeper explicitly records accept, retry, or escalation.
8. On accept, Review Control calls Workspace merge.
9. If merge succeeds, Workflow Policy transitions the task to `accepted`.
10. If merge fails, Review Control creates a new review ticket with merge-failure context.
11. Workflow Policy checks completion.

### 7.3 User-question flow

1. Gatekeeper calls `request_user_decision`.
2. QuestionStore persists a stable question record.
3. Workflow Policy blocks the dependent path.
4. User answer is sent to Control Plane.
5. Control Plane resolves the question and forwards the answer into the active Gatekeeper session.
6. Gatekeeper continues via MCP commands.

## 8. Required Redesigns

These are hard redesign points. They are not optional cleanup.

- Remove `GatekeeperRunResult` as an orchestration mutation carrier.
- Remove or drastically shrink `StateStore.apply_gatekeeper_result()`.
- Remove review decision inference from `ReviewService.resolve_decision()`.
- Remove text-based question reconciliation as an authority mechanism.
- Split questions out of `state.json`.
- Detach `TaskDispatcher` from `RoadmapService`.
- Replace generic `update_task(status=...)` style APIs with semantic transition commands.
- Change workflow completion logic so a long-lived Gatekeeper does not block completion.
- Update `docs/spec.md` so Gatekeeper no longer directly owns `consensus.md` writes.

Decision:

- Workflow state is authoritative for orchestration lifecycle.
- Consensus is an orchestrator-owned artifact that the Gatekeeper updates through MCP.
- There must be no two-way auto-sync loop between workflow state and consensus state.

## 9. Workload and Feasibility

| Area | Workload | Feasibility | Notes |
|---|---:|---:|---|
| Gatekeeper Lifecycle extraction | Medium | High | Requires removing post-run mutation logic from the current Gatekeeper runtime service. |
| MCP as authoritative Gatekeeper mutation path | Medium | High | The current MCP surface already exists and can be expanded. |
| Execution Coordinator split from execution policy | Medium | High | Current runtime and workspace plumbing are reusable. |
| Workflow Policy redesign with explicit review states | High | Medium | Task state model must change. |
| Review Control and ReviewTicketStore | Medium | High | Necessary for async review and restart recovery. |
| Split QuestionStore from `state.json` | Medium | High | High value and conceptually clean. |
| Shrink `state.json` to authoritative session facts | Medium | Medium | Requires migration and removal of duplicated projections. |
| Detach scheduler from RoadmapStore | Medium | High | One of the clearest boundary fixes. |
| Replace generic task mutation API with semantic commands | Medium | High | Necessary to keep subsystem boundaries honest. |
| Spec alignment | Small | Mandatory | The written spec currently contradicts the target architecture. |

Overall feasibility:

- The redesign is feasible.
- The runtime and workspace layers are salvageable.
- The MCP layer is salvageable and should become central.
- The review path, question authority model, roadmap scheduler ownership, and state projection model must be redesigned rather than preserved.

## 10. Final Decisions

- The orchestrator owns the Gatekeeper lifecycle.
- The Gatekeeper controls planning and review decisions only through MCP.
- The orchestrator owns every durable file in `.vibrant/`.
- Review is asynchronous and explicit, not inferred.
- Questions use stable IDs and dedicated persistence.
- Scheduler state is not stored inside roadmap persistence.
- A long-lived Gatekeeper is compatible with workflow completion.
- `docs/spec.md` must be updated before implementation so the design and the spec stop contradicting each other.
