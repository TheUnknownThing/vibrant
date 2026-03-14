"""Shared types for the redesigned orchestrator."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, Protocol

from vibrant.agents.runtime import InputRequest, NormalizedRunResult, RunState
from vibrant.models.agent import AgentRunRecord
from vibrant.models.task import TaskStatus
from vibrant.providers.base import CanonicalEvent

if TYPE_CHECKING:
    from vibrant.providers.invocation import MCPAccessDescriptor

    from .interface.mcp.common import MCPPrincipal


def utc_now() -> str:
    """Return an ISO 8601 UTC timestamp."""

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class WorkflowStatus(str, Enum):
    INIT = "init"
    PLANNING = "planning"
    EXECUTING = "executing"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class GatekeeperLifecycleStatus(str, Enum):
    NOT_STARTED = "not_started"
    STARTING = "starting"
    RUNNING = "running"
    AWAITING_USER = "awaiting_user"
    IDLE = "idle"
    FAILED = "failed"
    STOPPED = "stopped"


class AttemptStatus(str, Enum):
    LEASED = "leased"
    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"
    VALIDATION_PENDING = "validation_pending"
    VALIDATING = "validating"
    REVIEW_PENDING = "review_pending"
    MERGE_PENDING = "merge_pending"
    RETRY_PENDING = "retry_pending"
    FAILED = "failed"
    ACCEPTED = "accepted"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"


class QuestionPriority(str, Enum):
    BLOCKING = "blocking"
    NORMAL = "normal"


QuestionScope = str


class QuestionStatus(str, Enum):
    PENDING = "pending"
    WITHDRAWN = "withdrawn"
    RESOLVED = "resolved"


class ReviewTicketStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    RETRY = "retry"
    ESCALATED = "escalated"


@dataclass(slots=True)
class GatekeeperSessionSnapshot:
    agent_id: str | None = None
    run_id: str | None = None
    conversation_id: str | None = None
    lifecycle_state: GatekeeperLifecycleStatus = GatekeeperLifecycleStatus.NOT_STARTED
    provider_thread_id: str | None = None
    active_turn_id: str | None = None
    resumable: bool = False
    last_error: str | None = None
    updated_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class BoundAgentCapabilities:
    principal: MCPPrincipal
    tool_names: list[str]
    resource_names: list[str]
    provider_binding: Mapping[str, Any]
    access: MCPAccessDescriptor | None = None
    mcp_server: Any | None = None


@dataclass(slots=True)
class ValidationOutcome:
    status: Literal["passed", "failed", "skipped", "cancelled"]
    run_ids: list[str]
    summary: str | None = None
    results_ref: str | None = None


@dataclass(slots=True)
class AttemptRecord:
    attempt_id: str
    task_id: str
    status: AttemptStatus
    workspace_id: str
    code_run_id: str | None
    validation_run_ids: list[str]
    merge_run_id: str | None
    task_definition_version: int
    conversation_id: str | None
    created_at: str
    updated_at: str


@dataclass(slots=True)
class AttemptCompletion:
    attempt_id: str
    task_id: str
    status: Literal["succeeded", "failed", "awaiting_input", "cancelled"]
    code_run_id: str
    workspace_ref: str
    diff_ref: str | None
    validation: ValidationOutcome | None
    summary: str | None
    error: str | None
    conversation_ref: str | None
    provider_events_ref: str | None


@dataclass(slots=True)
class DiffArtifact:
    workspace_id: str
    path: str
    summary: str | None = None


@dataclass(slots=True)
class MergeOutcome:
    status: Literal["merged", "conflicted", "failed"]
    message: str | None = None
    follow_up_required: bool = False


@dataclass(slots=True)
class WorkspaceHandle:
    workspace_id: str
    task_id: str
    path: str
    branch: str
    base_branch: str


@dataclass(slots=True)
class ReviewTicket:
    ticket_id: str
    task_id: str
    attempt_id: str
    run_id: str
    review_kind: Literal["task_result", "merge_failure"]
    conversation_id: str | None
    status: ReviewTicketStatus = ReviewTicketStatus.PENDING
    summary: str | None = None
    diff_ref: str | None = None
    created_at: str = field(default_factory=utc_now)
    resolved_at: str | None = None
    resolution_reason: str | None = None


@dataclass(slots=True)
class ReviewResolutionRecord:
    ticket_id: str
    task_id: str
    attempt_id: str
    decision: Literal["accept", "retry", "escalate"]
    applied: bool
    merge_outcome: MergeOutcome | None
    follow_up_ticket_id: str | None


@dataclass(slots=True)
class QuestionRecord:
    question_id: str
    text: str
    priority: QuestionPriority
    source_role: str
    source_agent_id: str | None
    source_conversation_id: str | None
    source_turn_id: str | None
    blocking_scope: str
    task_id: str | None = None
    status: QuestionStatus = QuestionStatus.PENDING
    answer: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    withdrawn_reason: str | None = None


@dataclass(slots=True)
class AgentStreamEvent:
    conversation_id: str
    entry_id: str
    source_event_id: str | None
    sequence: int
    agent_id: str | None
    run_id: str | None
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


@dataclass(slots=True)
class AgentConversationEntry:
    role: Literal["user", "assistant", "tool", "system"]
    kind: Literal["message", "thinking", "tool_call", "status", "error"]
    turn_id: str | None
    text: str
    payload: Mapping[str, Any] | None
    started_at: str | None
    finished_at: str | None


@dataclass(slots=True)
class AgentConversationView:
    conversation_id: str
    agent_ids: list[str]
    task_ids: list[str]
    active_turn_id: str | None
    entries: list[AgentConversationEntry]
    updated_at: str | None


AgentStreamCallback = Callable[[AgentStreamEvent], Any]
CanonicalEventHandler = Callable[[CanonicalEvent], Any]


class StreamSubscription(Protocol):
    def close(self) -> None: ...


@dataclass(slots=True)
class WorkflowState:
    session_id: str
    started_at: str
    workflow_status: WorkflowStatus
    concurrency_limit: int
    gatekeeper_session: GatekeeperSessionSnapshot
    resume_status: WorkflowStatus | None = None
    total_agent_spawns: int = 0


@dataclass(slots=True)
class WorkflowSnapshot:
    status: WorkflowStatus
    concurrency_limit: int
    gatekeeper: GatekeeperSessionSnapshot
    pending_question_ids: tuple[str, ...]
    active_attempt_ids: tuple[str, ...]
    active_agent_ids: tuple[str, ...]


@dataclass(slots=True)
class RuntimeHandleSnapshot:
    agent_id: str
    run_id: str
    state: str
    provider_thread_id: str | None
    awaiting_input: bool
    input_requests: list[InputRequest]


@dataclass(slots=True)
class RuntimeExecutionResult:
    agent_record: AgentRunRecord
    events: list[CanonicalEvent] = field(default_factory=list)
    summary: str | None = None
    error: str | None = None
    turn_result: Any | None = None
    state: RunState | None = None
    awaiting_input: bool = False
    provider_thread_id: str | None = None
    provider_thread_path: str | None = None
    provider_resume_cursor: dict[str, Any] | None = None
    input_requests: list[InputRequest] = field(default_factory=list)
    normalized_result: NormalizedRunResult | None = None


@dataclass(slots=True)
class AgentOutputSegment:
    kind: str
    text: str
    timestamp: datetime | None = None


@dataclass(slots=True)
class AgentProgressItem:
    message: str
    item_type: str | None = None
    timestamp: datetime | None = None


@dataclass(slots=True)
class AgentOutputError:
    message: str
    raw: dict[str, Any] | None = None
    timestamp: datetime | None = None


@dataclass(slots=True)
class AgentThinkingState:
    text: str = ""
    status: str = "idle"
    item_id: str | None = None
    timestamp: datetime | None = None


@dataclass(slots=True)
class AgentOutput:
    agent_id: str
    task_id: str
    turn_id: str | None = None
    status: str = "idle"
    phase: str = "idle"
    segments: list[AgentOutputSegment] = field(default_factory=list)
    partial_text: str = ""
    progress: list[AgentProgressItem] = field(default_factory=list)
    pending_requests: list[InputRequest] = field(default_factory=list)
    thinking: AgentThinkingState = field(default_factory=AgentThinkingState)
    error: AgentOutputError | None = None
    updated_at: datetime | None = None
    canonical_event_log: str | None = None


@dataclass(slots=True)
class AgentSnapshotIdentity:
    agent_id: str
    run_id: str
    task_id: str
    role: str
    agent_type: str | None = None


@dataclass(slots=True)
class AgentSnapshotRuntime:
    status: str
    state: str
    has_handle: bool
    active: bool
    done: bool
    awaiting_input: bool
    pid: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    input_requests: list[InputRequest] = field(default_factory=list)


@dataclass(slots=True)
class AgentSnapshotWorkspace:
    branch: str | None = None
    worktree_path: str | None = None


@dataclass(slots=True)
class AgentSnapshotOutcome:
    summary: str | None = None
    error: str | None = None
    output: AgentOutput | None = None


@dataclass(slots=True)
class AgentSnapshotProvider:
    thread_id: str | None = None
    thread_path: str | None = None
    resume_cursor: dict[str, Any] | None = None
    native_event_log: str | None = None
    canonical_event_log: str | None = None


@dataclass(slots=True)
class OrchestratorAgentSnapshot:
    identity: AgentSnapshotIdentity
    runtime: AgentSnapshotRuntime
    workspace: AgentSnapshotWorkspace = field(default_factory=AgentSnapshotWorkspace)
    outcome: AgentSnapshotOutcome = field(default_factory=AgentSnapshotOutcome)
    provider: AgentSnapshotProvider = field(default_factory=AgentSnapshotProvider)


@dataclass(slots=True)
class TaskResult:
    task_id: str | None
    outcome: str
    task_status: TaskStatus | None = None
    agent_record: AgentRunRecord | None = None
    gatekeeper_result: NormalizedRunResult | None = None
    merge_result: MergeOutcome | None = None
    events: list[CanonicalEvent] = field(default_factory=list)
    summary: str | None = None
    error: str | None = None
    worktree_path: str | None = None


def dataclass_dict(value: Any) -> Any:
    """Convert nested dataclass values into plain Python structures."""

    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return value
