"""Shared types for the redesigned orchestrator."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, Protocol

from vibrant.agents.runtime import InputRequest
from vibrant.models.agent import AgentStatus, ProviderResumeHandle
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


class WorkspaceKind(str, Enum):
    TASK = "task"
    INTEGRATION = "integration"


class WorkspaceStatus(str, Enum):
    ACTIVE = "active"
    NO_CHANGES = "no_changes"
    RESULT_CAPTURED = "result_captured"
    INTEGRATING = "integrating"
    MERGED = "merged"
    CONFLICTED = "conflicted"
    FAILED = "failed"


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
class AgentMCPBinding:
    principal: MCPPrincipal
    access: MCPAccessDescriptor


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
class AttemptRecoveryState:
    attempt_id: str
    task_id: str
    status: AttemptStatus
    run_id: str | None
    run_status: str | None
    run_stop_reason: str | None
    workspace_path: str | None
    live: bool = False


@dataclass(slots=True)
class AttemptExecutionView:
    attempt_id: str
    task_id: str
    status: AttemptStatus
    workspace_id: str
    conversation_id: str | None
    run_id: str | None
    run_status: str | None
    run_stop_reason: str | None = None
    provider_thread_id: str | None = None
    resumable: bool = False
    live: bool = False
    awaiting_input: bool = False
    input_requests: list[InputRequest] = field(default_factory=list)
    updated_at: str | None = None


@dataclass(slots=True)
class AttemptExecutionSnapshot:
    attempt_id: str
    task_id: str
    status: AttemptStatus
    workspace_id: str
    workspace_path: str | None
    conversation_id: str | None
    run_id: str | None
    run_status: str | None
    run_stop_reason: str | None = None
    provider_resume_handle: ProviderResumeHandle | None = None
    provider_thread_id: str | None = None
    resumable: bool = False
    live: bool = False
    awaiting_input: bool = False
    input_requests: list[InputRequest] = field(default_factory=list)
    updated_at: str | None = None


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
    base_commit: str | None = None
    result_commit: str | None = None
    summary: str | None = None


@dataclass(slots=True)
class MergeOutcome:
    status: Literal["merged", "conflicted", "failed", "validation_failed", "dirty_target", "stale_target"]
    message: str | None = None
    follow_up_required: bool = False
    integration_commit: str | None = None


@dataclass(slots=True)
class WorkspaceHandle:
    workspace_id: str
    task_id: str
    path: str
    branch: str
    base_branch: str
    attempt_id: str | None = None
    kind: WorkspaceKind = WorkspaceKind.TASK
    target_ref: str = ""
    base_commit: str | None = None
    result_commit: str | None = None
    integration_commit: str | None = None
    status: WorkspaceStatus = WorkspaceStatus.ACTIVE
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


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
    base_commit: str | None = None
    result_commit: str | None = None
    integration_commit: str | None = None
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
class ConversationSummary:
    conversation_id: str
    agent_ids: list[str]
    task_ids: list[str]
    provider_thread_id: str | None = None
    active_turn_id: str | None = None
    latest_run_id: str | None = None
    updated_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class QuestionView:
    question_id: str
    text: str
    priority: QuestionPriority
    status: QuestionStatus
    blocking_scope: str
    task_id: str | None = None
    answer: str | None = None
    withdrawn_reason: str | None = None

    @classmethod
    def from_record(cls, record: QuestionRecord) -> "QuestionView":
        return cls(
            question_id=record.question_id,
            text=record.text,
            priority=record.priority,
            status=record.status,
            blocking_scope=record.blocking_scope,
            task_id=record.task_id,
            answer=record.answer,
            withdrawn_reason=record.withdrawn_reason,
        )


@dataclass(slots=True)
class AgentStreamEvent:
    conversation_id: str
    entry_id: str
    source_event_id: str | None
    sequence: int
    agent_id: str | None
    run_id: str | None
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
        "conversation.progress",
        "conversation.request.opened",
        "conversation.request.resolved",
        "conversation.turn.started",
        "conversation.turn.completed",
        "conversation.runtime.error",
    ]
    text: str | None
    payload: Mapping[str, Any] | None
    created_at: str
    task_id: str | None = None


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
    run_ids: list[str]
    active_turn_id: str | None
    entries: list[AgentConversationEntry]
    updated_at: str | None


AgentStreamCallback = Callable[[AgentStreamEvent], Any]
CanonicalEventHandler = Callable[[CanonicalEvent], Any]
ProviderAdapterFactory = Callable[..., Any]


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
class WorkflowSessionSnapshot:
    session_id: str
    started_at: str
    status: WorkflowStatus
    resume_status: WorkflowStatus | None
    concurrency_limit: int
    gatekeeper: GatekeeperSessionSnapshot
    total_agent_spawns: int = 0
    pending_question_ids: tuple[str, ...] = ()
    active_attempt_ids: tuple[str, ...] = ()


@dataclass(slots=True)
class WorkflowSnapshot:
    status: WorkflowStatus
    concurrency_limit: int
    gatekeeper: GatekeeperSessionSnapshot
    pending_question_ids: tuple[str, ...]
    active_attempt_ids: tuple[str, ...]


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
    run_id: str
    agent_id: str
    role: str
    status: AgentStatus
    summary: str | None = None
    error: str | None = None
    awaiting_input: bool = False
    provider_events_ref: str | None = None
    provider_thread_id: str | None = None
    input_requests: list[InputRequest] = field(default_factory=list)


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
class RoleSnapshot:
    role: str
    scope_types: tuple[str, ...] = ()
    instance_count: int = 0
    active_run_count: int = 0


@dataclass(slots=True)
class AgentInstanceIdentitySnapshot:
    agent_id: str
    role: str


@dataclass(slots=True)
class AgentInstanceScopeSnapshot:
    scope_type: str
    scope_id: str | None = None


@dataclass(slots=True)
class AgentInstanceProviderSnapshot:
    kind: str
    transport: str
    runtime_mode: str


@dataclass(slots=True)
class AgentInstanceSnapshot:
    identity: AgentInstanceIdentitySnapshot
    scope: AgentInstanceScopeSnapshot
    provider: AgentInstanceProviderSnapshot
    latest_run_id: str | None = None
    active_run_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class AgentRunIdentitySnapshot:
    agent_id: str
    run_id: str
    role: str


@dataclass(slots=True)
class AgentRunRuntimeSnapshot:
    status: str
    state: str
    has_handle: bool
    active: bool
    done: bool
    awaiting_input: bool
    stop_reason: str | None = None
    pid: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    input_requests: list[InputRequest] = field(default_factory=list)


@dataclass(slots=True)
class AgentRunWorkspaceSnapshot:
    branch: str | None = None
    worktree_path: str | None = None


@dataclass(slots=True)
class AgentRunOutcomeSnapshot:
    summary: str | None = None
    error: str | None = None
    output: AgentOutput | None = None


@dataclass(slots=True)
class AgentRunProviderSnapshot:
    thread_id: str | None = None
    thread_path: str | None = None
    resume_cursor: dict[str, Any] | None = None
    native_event_log: str | None = None
    canonical_event_log: str | None = None


@dataclass(slots=True)
class AgentRunSnapshot:
    identity: AgentRunIdentitySnapshot
    runtime: AgentRunRuntimeSnapshot
    workspace: AgentRunWorkspaceSnapshot = field(default_factory=AgentRunWorkspaceSnapshot)
    outcome: AgentRunOutcomeSnapshot = field(default_factory=AgentRunOutcomeSnapshot)
    provider: AgentRunProviderSnapshot = field(default_factory=AgentRunProviderSnapshot)


@dataclass(slots=True)
class TaskResult:
    task_id: str | None
    outcome: str
    summary: str | None = None
    error: str | None = None
    worktree_path: str | None = None


def dataclass_dict(value: Any) -> Any:
    """Convert nested dataclass values into plain Python structures."""

    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return value
