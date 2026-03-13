# Orchestrator Redesign Proposal

## 1. Design Rules

The redesign is based on six non-negotiable rules.

- The orchestrator owns all durable state under `.vibrant/`.
- The orchestrator owns the lifecycle of every agent, including the Gatekeeper.
- The Gatekeeper never mutates orchestrator state by writing files or by prose output.
- The Gatekeeper mutates orchestrator state only through typed MCP tools.
- The orchestrator never infers planning or review decisions from agent run results, roadmap diffs, or free-form text.
- The orchestrator owns processed agent conversation streams and their durable history; provider logs are observability artifacts, not the TUI contract.

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
- route user chat, user answers, and high-level workflow commands
- coordinate Gatekeeper, worker execution, review waiting, merge follow-up, and completion
- record host-originated conversation entries before they are sent to agents
- publish canonical runtime events onto the orchestrator event bus
- expose read models and subscriptions to the TUI and other first-party consumers

It must not parse markdown, manage worktrees directly, or speak provider-specific runtime protocols.

Primary interface:

```python
@dataclass
class GatekeeperSubmission:
    submission_id: str
    session: GatekeeperSessionSnapshot
    conversation_id: str
    agent_id: str | None
    accepted: bool
    active_turn_id: str | None
    error: str | None = None

class OrchestratorControlPlane:
    async def submit_user_message(self, text: str) -> GatekeeperSubmission: ...
    async def answer_user_decision(self, question_id: str, answer: str) -> GatekeeperSubmission: ...
    async def start_execution(self) -> WorkflowSnapshot: ...
    async def pause_workflow(self) -> WorkflowSnapshot: ...
    async def resume_workflow(self) -> WorkflowSnapshot: ...
    async def restart_gatekeeper(self, reason: str | None = None) -> GatekeeperSessionSnapshot: ...
    async def stop_gatekeeper(self) -> GatekeeperSessionSnapshot: ...
    def conversation(self, conversation_id: str) -> AgentConversationView | None: ...
    def subscribe_conversation(
        self,
        conversation_id: str,
        callback: AgentStreamCallback,
        *,
        replay: bool = False,
    ) -> StreamSubscription: ...
    def snapshot(self) -> OrchestratorSnapshot: ...
```

Decision:

- Public consumers should get a submission receipt plus conversation subscription, not a raw runtime handle.

### 3.2 Agent Session Binding

Purpose: own the missing seam between agent lifecycle and the orchestrator MCP/authz surface.

Responsibilities:

- create per-agent capability bindings
- choose the MCP principal and allowed scope for Gatekeeper, workers, validators, and merge agents
- provide provider-facing binding metadata needed to expose those capabilities inside a session/thread
- keep MCP wiring out of Gatekeeper Lifecycle and worker execution code

Primary interface:

```python
@dataclass
class BoundAgentCapabilities:
    principal: MCPPrincipal
    mcp_server: OrchestratorMCPServer
    tool_names: list[str]
    resource_names: list[str]
    provider_binding: Mapping[str, Any]

class AgentSessionBindingService:
    def bind_gatekeeper(self, *, session_id: str, conversation_id: str | None) -> BoundAgentCapabilities: ...
    def bind_worker(self, *, agent_id: str, task_id: str, agent_type: str) -> BoundAgentCapabilities: ...
```

Decision:

- MCP scope attachment is its own subsystem. It must not be hidden inside Gatekeeper Lifecycle or the provider adapter.

### 3.3 Gatekeeper Lifecycle

Purpose: own Gatekeeper runtime lifecycle only.

Responsibilities:

- spawn the Gatekeeper
- resume the Gatekeeper from persisted provider-thread metadata
- attach the previously computed capability binding
- track session state and health
- send messages into the active Gatekeeper session
- interrupt, stop, and restart the active Gatekeeper turn/session
- use the same runtime control and result primitives as every other agent run

It must not:

- write roadmap or consensus
- resolve questions
- apply workflow transitions
- interpret free-form Gatekeeper text

Primary interface:

```python
class GatekeeperLifecycleService:
    async def ensure_session(self) -> GatekeeperSessionSnapshot: ...
    async def resume_or_start(self) -> GatekeeperSessionSnapshot: ...
    async def submit(
        self,
        *,
        message_kind: GatekeeperMessageKind,
        text: str,
        submission_id: str,
        resume: bool = True,
    ) -> AgentHandle: ...
    async def interrupt_active_turn(self) -> GatekeeperSessionSnapshot: ...
    async def stop_session(self) -> GatekeeperSessionSnapshot: ...
    async def restart_session(self, *, reason: str | None = None) -> GatekeeperSessionSnapshot: ...
    def snapshot(self) -> GatekeeperSessionSnapshot: ...
```

Required type:

```python
@dataclass
class GatekeeperSessionSnapshot:
    agent_id: str | None
    conversation_id: str | None
    lifecycle_state: Literal[
        "not_started", "starting", "running", "awaiting_user",
        "idle", "failed", "stopped"
    ]
    provider_thread_id: str | None
    active_turn_id: str | None
    resumable: bool
    last_error: str | None
```

Decision:

- The Gatekeeper must use the same `AgentHandle` and normalized run-result contract as every other agent.
- The handle remains an internal runtime-control primitive for await, interrupt, kill, resume, and provider request response.

### 3.4 MCP Control Surface

Purpose: the authoritative control protocol used by the Gatekeeper and, later, by other agent roles with narrower scopes.

Responsibilities:

- expose read resources and semantic write tools
- enforce role-specific permissions through the binding service
- validate all arguments
- translate MCP calls into command handlers, never direct file patching

For the Gatekeeper, MCP is not a convenience layer. It is the mutation path.

Required read resources:

```python
get_consensus() -> ConsensusView
get_roadmap() -> RoadmapView
get_task(task_id: str) -> TaskView
get_workflow_status() -> WorkflowStatusView
list_pending_questions() -> list[QuestionView]
list_active_agents() -> list[AgentRuntimeView]
list_active_attempts() -> list[AttemptView]
get_review_ticket(ticket_id: str) -> ReviewTicketView | None
list_pending_review_tickets() -> list[ReviewTicketView]
list_recent_events(limit: int = 20) -> list[DomainEventView]
```

Required write tools:

```python
update_consensus(...) -> ConsensusView
add_task(...) -> TaskView
update_task_definition(...) -> TaskView
reorder_tasks(task_ids: list[str]) -> RoadmapView
request_user_decision(...) -> QuestionView
withdraw_question(question_id: str, reason: str | None = None) -> QuestionView
end_planning_phase() -> WorkflowStatusView
pause_workflow() -> WorkflowStatusView
resume_workflow() -> WorkflowStatusView
accept_review_ticket(ticket_id: str) -> ReviewResolutionView
retry_review_ticket(
    ticket_id: str,
    failure_reason: str,
    prompt_patch: str | None = None,
    acceptance_patch: Sequence[str] | None = None,
) -> ReviewResolutionView
escalate_review_ticket(ticket_id: str, reason: str) -> ReviewResolutionView
```

Decisions:

- Do not keep `review_task_outcome(decision=...)`.
- Do not keep `set_pending_questions(...)` as an authority path.
- User answers are host-owned; the Gatekeeper may request or withdraw a question, but it does not resolve user input.

### 3.5 Workflow Policy

Purpose: own task-level workflow policy, dispatch eligibility, and completion rules.

Responsibilities:

- dependency scheduling
- dispatch eligibility
- concurrency rules
- user-blocking and Gatekeeper-failure blocking rules
- task-level state transitions
- workflow completion detection

Task states:

- `pending`
- `ready`
- `active`
- `review_pending`
- `blocked`
- `accepted`
- `escalated`

Attempt states are separate and live in the attempt model:

- `leased`
- `running`
- `awaiting_input`
- `validation_pending`
- `validating`
- `review_pending`
- `merge_pending`
- `retry_pending`
- `accepted`
- `escalated`
- `cancelled`

Decision:

- Task state and attempt state must remain separate.
- Worker completion, validation completion, review resolution, and task acceptance are different lifecycle points.

Primary interface:

```python
class WorkflowPolicyService:
    def snapshot(self) -> WorkflowSnapshot: ...
    def select_next(self, *, limit: int) -> list[DispatchLease]: ...
    def on_attempt_started(self, attempt: AttemptRecord) -> WorkflowSnapshot: ...
    def on_attempt_completed(self, completion: AttemptCompletion) -> WorkflowSnapshot: ...
    def on_review_ticket_created(self, ticket: ReviewTicket) -> WorkflowSnapshot: ...
    def mark_task_accepted(self, *, task_id: str, attempt_id: str) -> WorkflowSnapshot: ...
    def requeue_task(self, *, task_id: str, attempt_id: str) -> WorkflowSnapshot: ...
    def mark_task_blocked(self, *, task_id: str, reason: str) -> WorkflowSnapshot: ...
    def mark_task_escalated(self, *, task_id: str, attempt_id: str) -> WorkflowSnapshot: ...
    def maybe_complete(self) -> WorkflowSnapshot: ...
```

Required types:

```python
@dataclass
class DispatchLease:
    task_id: str
    lease_id: str
    task_definition_version: int
    branch_hint: str | None

@dataclass
class AttemptRecord:
    attempt_id: str
    task_id: str
    status: Literal[
        "leased", "running", "awaiting_input", "validation_pending",
        "validating", "review_pending", "merge_pending",
        "retry_pending", "accepted", "escalated", "cancelled"
    ]
    workspace_id: str
    code_agent_id: str | None
    validation_agent_ids: list[str]
    merge_agent_id: str | None
    task_definition_version: int
    conversation_id: str | None
    created_at: str
    updated_at: str

@dataclass
class ValidationOutcome:
    status: Literal["passed", "failed", "skipped", "cancelled"]
    agent_ids: list[str]
    summary: str | None
    results_ref: str | None

@dataclass
class AttemptCompletion:
    attempt_id: str
    task_id: str
    status: Literal["succeeded", "failed", "awaiting_input", "cancelled"]
    code_agent_id: str
    workspace_ref: str
    diff_ref: str | None
    validation: ValidationOutcome | None
    summary: str | None
    error: str | None
    conversation_ref: str | None
    provider_events_ref: str | None
```

### 3.6 Execution Coordinator

Purpose: run all worker-side stages for one attempt mechanically.

Responsibilities:

- prepare workspace
- freeze the task definition version used by the attempt
- assemble task prompt and injected context
- create attempt and agent records
- start the code agent
- await code completion
- run validation/test agents when required
- collect diff and artifact references
- return `AttemptCompletion`

It must not:

- decide retry or escalation
- call the Gatekeeper directly
- mark tasks accepted
- complete workflow

Primary interface:

```python
class ExecutionCoordinator:
    async def start_attempt(self, lease: DispatchLease) -> AttemptRecord: ...
    async def await_attempt_completion(self, attempt_id: str) -> AttemptCompletion: ...
```

Decision:

- Validation is part of execution orchestration, not of review.
- Review should only start once code and validation evidence are both available.

### 3.7 Review Control

Purpose: own asynchronous review-ticket lifecycle and post-review resolution.

Responsibilities:

- create review tickets from completed attempts
- persist the review context durably
- expose tickets through MCP resources
- accept explicit review commands from the Gatekeeper
- apply review resolution by calling workflow policy and workspace mechanics
- on accept, initiate merge and create a follow-up ticket if merge fails

Primary interface:

```python
@dataclass
class ReviewResolutionCommand:
    decision: Literal["accept", "retry", "escalate"]
    failure_reason: str | None = None
    prompt_patch: str | None = None
    acceptance_patch: Sequence[str] | None = None

@dataclass
class ReviewResolutionRecord:
    ticket_id: str
    task_id: str
    attempt_id: str
    decision: Literal["accept", "retry", "escalate"]
    applied: bool
    merge_outcome: MergeOutcome | None
    follow_up_ticket_id: str | None

class ReviewControlService:
    def create_ticket(self, completion: AttemptCompletion, diff: DiffArtifact | None) -> ReviewTicket: ...
    def get_ticket(self, ticket_id: str) -> ReviewTicket | None: ...
    def list_pending(self) -> list[ReviewTicket]: ...
    def resolve(self, ticket_id: str, command: ReviewResolutionCommand) -> ReviewResolutionRecord: ...
```

Decisions:

- Review tickets are ticket-scoped and attempt-scoped, not task-singletons.
- ReviewControl is the single resolution-application authority. WorkflowPolicy does not expose a second decision API.

### 3.8 Runtime

Purpose: generic provider/runtime mechanism shared by Gatekeeper and worker agents.

Responsibilities:

- start, resume, wait, interrupt, and kill agent runs
- track live handles
- resolve provider-thread metadata
- publish canonical runtime events to the orchestrator event bus
- provide runtime snapshots

It must not:

- shape TUI conversation streams
- parse tool-call progress into conversation entries
- own question, review, or workflow mutation logic

Stable interface:

```python
class AgentRuntimeService:
    async def start_run(...) -> AgentHandle: ...
    async def resume_run(...) -> AgentHandle: ...
    async def wait_for_run(...) -> RuntimeExecutionResult: ...
    async def interrupt_run(...) -> RuntimeHandleSnapshot: ...
    async def kill_run(...) -> RuntimeHandleSnapshot: ...
    def snapshot_handle(...) -> RuntimeHandleSnapshot: ...
    def subscribe_canonical_events(
        self,
        callback: CanonicalEventHandler,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        event_types: Sequence[str] | None = None,
    ) -> EventSubscription: ...
```

Required canonical-event contract additions:

- Every canonical event must carry a stable `event_id`.
- Every canonical event stream must carry a replay-safe ordering token such as `sequence`.
- Raw reasoning text must not appear in canonical events; only summary-level reasoning events are allowed.
- Assistant message lifecycle must be explicit:
  - `assistant.message.delta`
  - `assistant.message.completed`
- Tool call lifecycle must be explicit:
  - `tool.call.started`
  - `tool.call.delta`
  - `tool.call.completed`
- Request events must include a user-facing message when available.

Decision:

- Runtime publishes canonical events only. Conversation shaping happens elsewhere.

### 3.9 Conversation Stream

Purpose: own TUI-facing conversation projection, durable conversation history, and live stream subscriptions.

Responsibilities:

- record host-originated messages before they are sent to agents
- bind agent runs into stable conversation ids
- translate canonical runtime events into processed conversation frames
- append conversation frames durably
- invoke live subscribers
- rebuild conversation history after restart or Gatekeeper resume

It must not:

- mutate roadmap, consensus, questions, or workflow state
- depend on provider-native wire events as its primary source
- store raw hidden reasoning

Primary interface:

```python
class ConversationStreamService:
    def bind_agent(
        self,
        *,
        conversation_id: str,
        agent_id: str,
        task_id: str | None,
        provider_thread_id: str | None = None,
    ) -> None: ...
    def record_host_message(
        self,
        *,
        conversation_id: str,
        role: Literal["user", "system"],
        text: str,
        related_question_id: str | None = None,
    ) -> AgentStreamEvent: ...
    def ingest_canonical(self, event: CanonicalEvent) -> list[AgentStreamEvent]: ...
    def rebuild(self, conversation_id: str) -> AgentConversationView | None: ...
    def subscribe(
        self,
        conversation_id: str,
        callback: AgentStreamCallback,
        *,
        replay: bool = False,
    ) -> StreamSubscription: ...
```

Required types:

```python
@dataclass
class AgentStreamEvent:
    conversation_id: str
    entry_id: str
    source_event_id: str | None
    sequence: int
    agent_id: str | None
    task_id: str | None
    turn_id: str | None
    item_id: str | None
    type: Literal[
        "conversation.user.message",
        "conversation.assistant.message.delta",
        "conversation.assistant.message.completed",
        "conversation.assistant.thinking.delta",
        "conversation.assistant.thinking.completed",
        "conversation.tool_call.started",
        "conversation.tool_call.delta",
        "conversation.tool_call.completed",
        "conversation.request.opened",
        "conversation.request.resolved",
        "conversation.turn.started",
        "conversation.turn.completed",
        "conversation.runtime.error",
    ]
    text: str | None
    payload: Mapping[str, Any] | None
    created_at: str

@dataclass
class AgentConversationEntry:
    role: Literal["user", "assistant", "tool", "system"]
    kind: Literal["message", "thinking", "tool_call", "status", "error"]
    turn_id: str | None
    text: str
    payload: Mapping[str, Any] | None
    started_at: str | None
    finished_at: str | None

@dataclass
class AgentConversationView:
    conversation_id: str
    agent_ids: list[str]
    task_ids: list[str]
    active_turn_id: str | None
    entries: list[AgentConversationEntry]
    updated_at: str | None

AgentStreamCallback = Callable[[AgentStreamEvent], Any]

class StreamSubscription(Protocol):
    def close(self) -> None: ...
```

Decisions:

- The TUI consumes `AgentStreamEvent`, not raw canonical provider events.
- Conversation history is reconstructed from stored frames, not from provider logs.
- Provider logs remain fallback/debugging artifacts only.

### 3.10 Workspace

Purpose: own worktree, reset, merge, and discard mechanics.

Responsibilities:

- create worktrees
- map branch names
- collect diffs
- reset workspaces between retry/merge operations when needed
- merge accepted work
- discard workspaces that are no longer needed

Primary interface:

```python
class WorkspaceService:
    def prepare_task_workspace(self, task_id: str, branch_hint: str | None = None) -> WorkspaceHandle: ...
    def collect_review_diff(self, workspace: WorkspaceHandle) -> DiffArtifact: ...
    def reset_workspace(self, workspace: WorkspaceHandle) -> None: ...
    def merge_task_result(self, workspace: WorkspaceHandle) -> MergeOutcome: ...
    def discard_workspace(self, workspace: WorkspaceHandle) -> None: ...
```

## 4. Durable Stores

### 4.1 WorkflowStateStore

Backing file: `.vibrant/state.json`

Persist only non-derivable workflow/session facts:

- `session_id`
- `started_at`
- `workflow_status`
- `concurrency_limit`
- `gatekeeper_session`
- `total_agent_spawns`

Do not persist:

- `active_agents`
- `provider_runtime`
- `pending_questions`
- `last_consensus_version`
- any derived conversation view

Required type and interface:

```python
@dataclass
class GatekeeperSessionState:
    lifecycle_status: GatekeeperLifecycleStatus
    agent_id: str | None
    conversation_id: str | None
    provider_thread_id: str | None
    active_turn_id: str | None
    last_error: str | None
    updated_at: str

class WorkflowStateStore:
    def load(self) -> WorkflowState: ...
    def save(self, state: WorkflowState) -> None: ...
    def update_workflow_status(self, status: WorkflowStatus) -> WorkflowState: ...
    def update_gatekeeper_session(self, session: GatekeeperSessionState) -> WorkflowState: ...
    def set_concurrency_limit(self, limit: int) -> WorkflowState: ...
```

Decision:

- Gatekeeper session state should be explicit instead of being inferred from several stores.

### 4.2 AttemptStore

Backing file: `.vibrant/attempts.json`

Decision:

- Attempt identity is mandatory for restart safety.
- Attempt state is persisted separately from roadmap task state.

Interface:

```python
class AttemptStore:
    def create(self, attempt: CreateAttempt) -> AttemptRecord: ...
    def get(self, attempt_id: str) -> AttemptRecord | None: ...
    def get_active_by_task(self, task_id: str) -> AttemptRecord | None: ...
    def list_active(self) -> list[AttemptRecord]: ...
    def update(self, attempt_id: str, patch: AttemptPatch) -> AttemptRecord: ...
```

### 4.3 QuestionStore

Backing file: `.vibrant/questions.json`

Decisions:

- Questions move out of `state.json`.
- Stable IDs are mandatory.
- Text reconciliation is removed as an authority mechanism.
- Question records must carry enough routing metadata to resume deterministically.

Required type and interface:

```python
@dataclass
class CreateQuestion:
    question_id: str | None
    text: str
    priority: QuestionPriority
    source_role: str
    source_agent_id: str | None
    source_conversation_id: str | None
    source_turn_id: str | None
    blocking_scope: Literal["planning", "workflow", "task", "review"]
    task_id: str | None

class QuestionStore:
    def list(self, *, status: QuestionStatus | None = None) -> list[QuestionRecord]: ...
    def list_pending(self) -> list[QuestionRecord]: ...
    def list_for_conversation(self, conversation_id: str) -> list[QuestionRecord]: ...
    def get(self, question_id: str) -> QuestionRecord | None: ...
    def create(self, question: CreateQuestion) -> QuestionRecord: ...
    def withdraw(self, question_id: str, *, reason: str | None = None) -> QuestionRecord: ...
    def resolve(self, question_id: str, *, answer: str | None) -> QuestionRecord: ...
```

### 4.4 ConsensusStore

Backing file: `.vibrant/consensus.md`

Decisions:

- The file remains human-readable.
- The Gatekeeper does not write it directly.
- The orchestrator writes it in response to MCP commands.
- Workflow status is not an authoritative consensus mutation. If status remains in markdown metadata, it is a one-way projection from workflow state.

Interface:

```python
class ConsensusStore:
    def load(self) -> ConsensusDocument | None: ...
    def write(self, document: ConsensusDocument) -> ConsensusDocument: ...
    def update_context(self, context: str) -> ConsensusDocument: ...
    def append_decision(...) -> ConsensusDocument: ...
```

### 4.5 RoadmapStore

Backing file: `.vibrant/roadmap.md`

Decisions:

- Remove scheduler ownership from roadmap persistence.
- Do not expose generic task status patching to outside callers.
- Active task definitions must be frozen or versioned so review and retry are deterministic.

Interface:

```python
class RoadmapStore:
    def load(self) -> RoadmapDocument: ...
    def write(self, document: RoadmapDocument) -> RoadmapDocument: ...
    def get_task(self, task_id: str) -> TaskInfo | None: ...
    def add_task(self, task: TaskInfo, index: int | None = None) -> RoadmapDocument: ...
    def update_task_definition(self, task_id: str, patch: TaskDefinitionPatch) -> TaskInfo: ...
    def definition_version(self, task_id: str) -> int: ...
    def record_task_state(self, task_id: str, state: TaskState, *, active_attempt_id: str | None = None) -> TaskInfo: ...
    def reorder_tasks(self, task_ids: list[str]) -> RoadmapDocument: ...
```

### 4.6 ReviewTicketStore

Backing file: `.vibrant/reviews.json`

Decision:

- Review tickets are attempt-scoped and may have multiple entries over the life of one task.

Required type and interface:

```python
@dataclass
class CreateReviewTicket:
    ticket_id: str | None
    task_id: str
    attempt_id: str
    agent_id: str
    review_kind: Literal["task_result", "merge_failure"]
    conversation_id: str | None

class ReviewTicketStore:
    def create(self, ticket: CreateReviewTicket) -> ReviewTicket: ...
    def get(self, ticket_id: str) -> ReviewTicket | None: ...
    def list_pending(self) -> list[ReviewTicket]: ...
    def list_by_task(self, task_id: str) -> list[ReviewTicket]: ...
    def list_by_attempt(self, attempt_id: str) -> list[ReviewTicket]: ...
    def resolve(self, ticket_id: str, resolution: ReviewResolution) -> ReviewTicket: ...
```

### 4.7 AgentRecordStore

Backing files: `.vibrant/agents/*.json`

Decisions:

- Keep it as the source of truth for durable per-agent records.
- Remove automatic global state rebuilds during upsert.
- `AgentExecutionContext` must carry `attempt_id` and `conversation_id`.

Interface:

```python
class AgentRecordStore:
    def get(self, agent_id: str) -> AgentRecord | None: ...
    def list(self) -> list[AgentRecord]: ...
    def list_by_task(self, task_id: str) -> list[AgentRecord]: ...
    def list_by_attempt(self, attempt_id: str) -> list[AgentRecord]: ...
    def upsert(self, record: AgentRecord, increment_spawn: bool = False) -> Path: ...
    def provider_thread(self, agent_id: str) -> ProviderThreadHandle | None: ...
```

### 4.8 ConversationStore

Backing files: `.vibrant/conversations/manifest.json` and `.vibrant/conversations/*.jsonl`

Decisions:

- The store persists manifests and append-only frames.
- Conversation views are projections and belong in read models, not in the raw store.
- The current `HistoryStore` / `ThreadInfo` path becomes a legacy adapter or import surface during migration.

Required types and interface:

```python
@dataclass
class ConversationManifest:
    conversation_id: str
    kind: Literal["gatekeeper", "worker"]
    task_id: str | None
    provider_thread_id: str | None
    agent_ids: list[str]
    created_at: str
    updated_at: str

class ConversationStore:
    def create_or_get(self, manifest: CreateConversation) -> ConversationManifest: ...
    def get_manifest(self, conversation_id: str) -> ConversationManifest | None: ...
    def attach_agent(self, conversation_id: str, agent_id: str) -> ConversationManifest: ...
    def append_frame(self, conversation_id: str, frame: AgentStreamEvent) -> int: ...
    def list_frames(self, conversation_id: str, *, after_seq: int | None = None) -> list[StoredAgentStreamEvent]: ...
```

### 4.9 EventLogStore

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
  - gatekeeper session state
  - blocking reason
  - concurrency limit

- `QuestionQueueReadModel`
  - pending and resolved questions with stable IDs and routing metadata

- `AttemptExecutionReadModel`
  - active attempts
  - attempt states
  - validation status
  - attempt-to-task and attempt-to-conversation joins

- `AgentRuntimeReadModel`
  - active agents
  - runtime state
  - resumable provider-thread data
  - summaries and errors

- `RoadmapExecutionReadModel`
  - roadmap
  - ready tasks
  - blocked tasks
  - dependency state
  - task definition versions
  - review-pending tasks

- `ConsensusSnapshotReadModel`
  - parsed consensus plus metadata
  - projected workflow status, if shown

- `ConversationHistoryReadModel`
  - active and historical conversations
  - reconstructed assistant output, summary-only thinking, and tool calls
  - stable conversation ids for Gatekeeper and worker runs

Decision:

- Replace durable projection fields like `state.active_agents` and `state.provider_runtime` with read-model computation.

## 6. Compatibility and Boundary Rules

These rules prevent gaps and overlaps between subsystems.

- Control Plane may orchestrate all major subsystems, but no other subsystem may coordinate the whole workflow.
- AgentSessionBindingService owns MCP/session binding. Gatekeeper Lifecycle and Execution Coordinator must not choose scopes themselves.
- Gatekeeper Lifecycle may depend on Runtime, AgentRecordStore, and AgentSessionBindingService, but not on RoadmapStore, ConsensusStore, QuestionStore, or WorkflowPolicy.
- MCP write handlers may call only semantic command services. They may not patch store internals directly.
- WorkflowPolicy owns task-level policy but not review resolution commands and not provider runtime mechanics.
- ExecutionCoordinator may use Runtime, Workspace, AttemptStore, AgentRecordStore, and prompt builders, but it may not apply review decisions or mutate workflow directly.
- ReviewControl is the single resolution-application authority for review tickets. It may use WorkflowPolicy and Workspace, but no second subsystem may apply the same review command.
- Runtime publishes canonical events but does not shape TUI streams.
- ConversationStreamService may depend on ConversationStore and canonical runtime events, but it may not mutate workflow state.
- TUI and MCP must read conversation history through `ConversationHistoryReadModel`, not directly from provider log files.
- Consensus status, if mirrored into markdown, is projection-only from workflow state. There must be no two-way auto-sync loop between workflow state and consensus state.
- Compatibility adapters for the stable facade, old MCP tool names, and legacy TUI history are transition layers only. They are not authoritative state owners.

## 7. End-to-End Flows

### 7.1 Planning flow

1. Control Plane ensures the Gatekeeper session exists.
2. AgentSessionBindingService binds Gatekeeper MCP capabilities for that session.
3. Control Plane records the outbound user message in the Gatekeeper conversation.
4. User message is submitted through Gatekeeper Lifecycle.
5. Runtime emits canonical events while the Gatekeeper responds.
6. ConversationStreamService converts those canonical events into durable conversation frames and live stream updates.
7. Gatekeeper reads roadmap, consensus, workflow, and questions through MCP.
8. Gatekeeper issues typed MCP commands to update roadmap, consensus, and questions.
9. Stores persist each change immediately.
10. Read models update.
11. Gatekeeper text remains informational only.

### 7.2 Execution flow

1. WorkflowPolicy selects ready tasks and issues `DispatchLease`.
2. ExecutionCoordinator starts an attempt, freezes the task-definition version, and starts the code agent.
3. Runtime canonical events are published and projected into worker conversation frames.
4. ExecutionCoordinator awaits code completion, runs validation agents as needed, collects diffs, and returns `AttemptCompletion`.
5. WorkflowPolicy marks the task `review_pending`.
6. ReviewControl creates a review ticket for the completed attempt.
7. Gatekeeper reads the review ticket through MCP.
8. Gatekeeper explicitly accepts, retries, or escalates the ticket through semantic review tools.
9. ReviewControl applies that resolution.
10. On accept, ReviewControl calls Workspace merge.
11. If merge succeeds, WorkflowPolicy marks the task accepted.
12. If merge fails, ReviewControl creates a follow-up merge-failure ticket.
13. WorkflowPolicy checks completion.

### 7.3 User-question flow

1. Gatekeeper calls `request_user_decision`.
2. QuestionStore persists a stable question record with routing metadata.
3. WorkflowPolicy blocks the dependent path.
4. User answer is recorded into the Gatekeeper conversation.
5. User answer is sent to Control Plane.
6. Control Plane resolves the question and forwards the answer into the active Gatekeeper session.
7. Gatekeeper continues via MCP commands.

### 7.4 Conversation recovery flow

1. On startup, ConversationStore loads manifests and stored frames.
2. ConversationHistoryReadModel rebuilds renderable history for the TUI from those stored frames.
3. If a conversation entry is missing or incomplete, canonical provider logs may be replayed as an explicit fallback.
4. When the Gatekeeper resumes the same provider thread, new frames append to the same stable conversation id.
5. TUI reads rebuilt history first and then subscribes for live stream events.

## 8. Required Redesigns

These are hard redesign points. They are not optional cleanup.

- Remove `GatekeeperRunResult` as an orchestration mutation carrier.
- Remove Gatekeeper-specific handle/result types as public orchestration concepts.
- Add an explicit `AgentSessionBindingService` for MCP/authz/session binding.
- Remove or drastically shrink `StateStore.apply_gatekeeper_result()`.
- Remove review decision inference from runtime output and from `ReviewService.resolve_decision()`.
- Remove text-based question reconciliation as an authority mechanism.
- Keep question resolution host-owned; add `withdraw_question(...)` for Gatekeeper-side cancellation/rephrasing.
- Split questions out of `state.json`.
- Introduce attempt-centric persistence via `AttemptStore`.
- Make review tickets attempt-scoped and ticket-scoped.
- Freeze or version task definitions for active attempts.
- Add a dedicated `ConversationStreamService` plus `ConversationStore` for TUI-facing conversation history.
- Remove raw reasoning text from canonical events; only summary-level reasoning may enter conversation history.
- Extend `vibrant/providers/base.py` with replay-safe canonical event identity plus first-class assistant-message and tool-call events.
- Stop treating imported conversations and provider logs as the primary conversation-history source for the TUI.
- Detach `TaskDispatcher` from `RoadmapStore`.
- Replace generic `update_task(status=...)` style APIs with semantic transition commands.
- Broaden `WorkspaceService` to cover reset/discard and merge-conflict workflows explicitly.
- Resolve stable API and MCP compatibility before deleting old facade/tool names.
- Update `docs/spec.md` so Gatekeeper no longer directly owns `consensus.md` writes.

Decision:

- Workflow state is authoritative for orchestration lifecycle.
- Consensus is an orchestrator-owned artifact that the Gatekeeper updates through MCP.
- There must be no two-way auto-sync loop between workflow state and consensus state.

## 9. Workload and Feasibility

| Area | Workload | Feasibility | Notes |
|---|---:|---:|---|
| Control Plane extraction | Medium | High | Current planning, question, and gatekeeper routing logic can be consolidated, but it is currently spread across multiple services. |
| Agent Session Binding | Medium | High | Necessary missing seam for MCP/authz/session scope attachment. |
| Gatekeeper Lifecycle cleanup | Medium | High | Runtime pieces are reusable once state mutation logic is removed. |
| MCP semantic command cleanup | Medium | High | Current MCP surface exists, but generic task/review/question mutations must be narrowed. |
| Workflow Policy + attempt model | High | Medium | Requires a real attempt store plus separation between task states and attempt states. |
| Execution Coordinator + validation stage | Medium | High | Current runtime/workspace plumbing is salvageable; validation ownership must be added explicitly. |
| Review Control + merge follow-up | High | Medium | Ticket-scoped async review and merge-failure follow-up do not exist yet. |
| Runtime canonical event contract | Medium | High | Provider stack is reusable, but canonical events must gain stable ids, assistant completion, and tool-call events. |
| Conversation store + history read model | High | Medium | Feasible after canonical contract changes; currently blocked by lossy message reconstruction and legacy TUI history overlap. |
| QuestionStore identity enrichment | Medium | High | Straightforward once question routing metadata is defined. |
| Store migration + compatibility layer | High | Medium | Necessary because the current stable facade/MCP/TUI contracts still preserve old review and Gatekeeper semantics. |
| Workflow/consensus authority cleanup | Medium | High | Must remove duplicated status authority before implementation. |
| Spec and stable API alignment | Small | Mandatory | `docs/spec.md` and `vibrant/orchestrator/STABLE_API.md` still contradict the target architecture. |

Overall feasibility:

- The redesign is feasible, but not as previously drafted.
- No subsystem is fundamentally impossible in this codebase.
- The runtime and workspace layers are salvageable.
- The main blockers are architectural seams and migration contracts, not missing core capability.
- The blocking redesign items are:
  - MCP/session binding ownership
  - attempt identity and validation ownership
  - canonical event contract for durable conversation history
  - stable API / MCP / TUI compatibility strategy
  - single authority for workflow status versus consensus status

## 10. Final Decisions

- The orchestrator owns the Gatekeeper lifecycle.
- The Gatekeeper controls planning and review decisions only through MCP.
- The orchestrator owns every durable file in `.vibrant/`.
- Add `AgentSessionBindingService` as a first-class subsystem.
- GatekeeperLifecycle is runtime-only and must not write orchestrator state.
- Gatekeeper-specific runtime handle/result types should not exist as orchestration concepts; generic agent runtime contracts are enough.
- Runtime publishes canonical events only; conversation shaping belongs to `ConversationStreamService`.
- Raw reasoning text must not enter canonical events or stored conversation history.
- Execution is attempt-centric and includes validation before review.
- Review tickets are attempt-scoped, and `ReviewControlService` is the single review-resolution authority.
- Active task definitions must be frozen or versioned.
- Question resolution is host-owned.
- Workflow state is authoritative; consensus status may only be a one-way projection if it remains in markdown metadata.
- Conversation history is a durable orchestrator artifact, not an incidental provider log.
- A compatibility layer is mandatory before removing the current stable facade/MCP names or the legacy TUI history path.
- `docs/spec.md` and `vibrant/orchestrator/STABLE_API.md` must be updated before implementation so the design and written contracts stop contradicting each other.
