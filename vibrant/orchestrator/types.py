"""Shared orchestration result and projection types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from vibrant.agents.gatekeeper import GatekeeperRunResult
from vibrant.agents.role_results import RoleResultPayload
from vibrant.agents.runtime import InputRequest, NormalizedRunResult, RunState
from vibrant.consensus import RoadmapDocument
from vibrant.models.agent import AgentRunRecord
from vibrant.models.consensus import ConsensusDocument
from vibrant.models.state import OrchestratorStatus, QuestionRecord
from vibrant.models.task import TaskStatus
from vibrant.orchestrator.execution.git_manager import GitMergeResult
from vibrant.providers.base import CanonicalEvent


@dataclass(slots=True, frozen=True)
class WorkflowSnapshot:
    """Stable workflow-layer projection."""

    status: OrchestratorStatus
    execution_mode: str | None
    user_input_banner: str
    notification_bell_enabled: bool


@dataclass(slots=True, frozen=True)
class DocumentSnapshot:
    """Stable document-layer projection."""

    roadmap: RoadmapDocument | None
    consensus: ConsensusDocument | None
    consensus_path: Path | None


@dataclass(slots=True, frozen=True)
class ProviderDefaultsSnapshot:
    """Stable provider defaults owned by an agent instance."""

    kind: str = "codex"
    transport: str = "app-server-json-rpc"
    runtime_mode: str = "workspace-write"


@dataclass(slots=True, frozen=True)
class AgentRunRef:
    """Light run summary embedded inside an instance snapshot."""

    run_id: str
    task_id: str | None
    lifecycle_status: str
    runtime_state: str
    summary: str | None = None
    error: str | None = None
    awaiting_input: bool = False
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(slots=True)
class TaskResult:
    """Structured outcome for one code-agent execution attempt."""

    task_id: str | None
    outcome: str
    task_status: TaskStatus | None = None
    agent_record: AgentRunRecord | None = None
    gatekeeper_result: GatekeeperRunResult | None = None
    merge_result: GitMergeResult | None = None
    events: list[CanonicalEvent] = field(default_factory=list)
    summary: str | None = None
    error: str | None = None
    worktree_path: str | None = None
    role_result: RoleResultPayload | None = None

    @property
    def workflow_outcome(self) -> str:
        return self.outcome

    @property
    def agent_id(self) -> str | None:
        if self.agent_record is None:
            return None
        return self.agent_record.identity.agent_id

    @property
    def run_id(self) -> str | None:
        if self.agent_record is None:
            return None
        return self.agent_record.identity.run_id

    @property
    def payload(self) -> RoleResultPayload | None:
        return self.role_result


@dataclass(slots=True, frozen=True)
class AgentRoleSnapshot:
    """Stable role-layer metadata exposed through the orchestrator facade."""

    role: str
    display_name: str
    workflow_class: str
    default_provider_kind: str
    default_runtime_mode: str
    supports_interactive_requests: bool
    persistent_thread: bool
    question_source_role: str | None = None
    contributes_control_plane_status: bool = False
    ui_model_name: str | None = None


RoleSnapshot = AgentRoleSnapshot


@dataclass(slots=True)
class AgentOutputSegment:
    """Committed renderable output emitted by an agent."""

    kind: str
    text: str
    timestamp: datetime | None = None


@dataclass(slots=True)
class AgentProgressItem:
    """Structured progress/status item derived from canonical events."""

    message: str
    item_type: str | None = None
    timestamp: datetime | None = None


@dataclass(slots=True)
class AgentOutputError:
    """Structured runtime error surfaced by an agent."""

    message: str
    raw: dict[str, Any] | None = None
    timestamp: datetime | None = None


@dataclass(slots=True)
class AgentThinkingState:
    """Structured reasoning/thinking state derived from canonical events."""

    text: str = ""
    status: str = "idle"
    item_id: str | None = None
    timestamp: datetime | None = None


@dataclass(slots=True)
class AgentOutput:
    """Processed, TUI-friendly projection of one agent's output state."""

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
    """Stable identifiers for one orchestrator-facing agent snapshot."""

    agent_id: str
    task_id: str | None
    role: str
    run_id: str | None = None
    scope_type: str | None = None
    scope_id: str | None = None


@dataclass(slots=True)
class AgentSnapshotRuntime:
    """Runtime and lifecycle state for one agent snapshot."""

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
    """Workspace-specific execution context for one agent snapshot."""

    branch: str | None = None
    worktree_path: str | None = None


@dataclass(slots=True)
class AgentSnapshotOutcome:
    """Best-known outcome/output view for one agent snapshot."""

    summary: str | None = None
    error: str | None = None
    output: AgentOutput | None = None
    role_result: Any | None = None


@dataclass(slots=True)
class AgentSnapshotProvider:
    """Provider/session metadata for one agent snapshot."""

    thread_id: str | None = None
    thread_path: str | None = None
    resume_cursor: dict[str, Any] | None = None
    native_event_log: str | None = None
    canonical_event_log: str | None = None


@dataclass(slots=True)
class AgentInstanceSnapshot:
    """Stable orchestrator-facing view of one agent instance and its latest run."""

    identity: AgentSnapshotIdentity
    runtime: AgentSnapshotRuntime
    workspace: AgentSnapshotWorkspace = field(default_factory=AgentSnapshotWorkspace)
    outcome: AgentSnapshotOutcome = field(default_factory=AgentSnapshotOutcome)
    provider: AgentSnapshotProvider = field(default_factory=AgentSnapshotProvider)
    agent_id: str | None = None
    role: str | None = None
    scope_type: str | None = None
    scope_id: str | None = None
    provider_defaults: ProviderDefaultsSnapshot | None = None
    supports_interactive_requests: bool = False
    persistent_thread: bool = False
    latest_run_id: str | None = None
    active_run_id: str | None = None
    active: bool | None = None
    awaiting_input: bool | None = None
    latest_run: AgentRunRef | None = None

    def __post_init__(self) -> None:
        self.agent_id = self.agent_id or self.identity.agent_id
        self.role = self.role or self.identity.role
        self.scope_type = self.scope_type or self.identity.scope_type
        self.scope_id = self.scope_id if self.scope_id is not None else self.identity.scope_id
        self.latest_run_id = self.latest_run_id or self.identity.run_id
        if self.active is None:
            self.active = self.runtime.active
        if self.awaiting_input is None:
            self.awaiting_input = self.runtime.awaiting_input
        if self.active_run_id is None and self.runtime.active:
            self.active_run_id = self.latest_run_id
        if self.latest_run is None and self.latest_run_id is not None:
            self.latest_run = AgentRunRef(
                run_id=self.latest_run_id,
                task_id=self.identity.task_id,
                lifecycle_status=self.runtime.status,
                runtime_state=self.runtime.state,
                summary=self.outcome.summary,
                error=self.outcome.error,
                awaiting_input=self.runtime.awaiting_input,
                started_at=self.runtime.started_at,
                finished_at=self.runtime.finished_at,
            )


@dataclass(slots=True, frozen=True)
class RunLifecycleSnapshot:
    """Durable lifecycle state for one execution run."""

    status: str
    pid: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(slots=True, frozen=True)
class RunRuntimeSnapshot:
    """Live runtime state for one execution run."""

    state: str
    active: bool
    done: bool
    awaiting_input: bool
    has_handle: bool = False
    input_requests: tuple[InputRequest, ...] = field(default_factory=tuple)


@dataclass(slots=True, frozen=True)
class RunWorkspaceSnapshot:
    """Workspace context for one execution run."""

    branch: str | None = None
    worktree_path: str | None = None


@dataclass(slots=True, frozen=True)
class RunProviderSnapshot:
    """Provider/session metadata for one execution run."""

    kind: str = "codex"
    transport: str = "app-server-json-rpc"
    runtime_mode: str = "workspace-write"
    provider_thread_id: str | None = None
    thread_path: str | None = None
    resume_cursor: dict[str, Any] | None = None
    native_event_log: str | None = None
    canonical_event_log: str | None = None

    @property
    def thread_id(self) -> str | None:
        return self.provider_thread_id


@dataclass(slots=True, frozen=True)
class RunEnvelope:
    """Role-neutral runtime envelope for one execution run."""

    state: str
    summary: str | None = None
    error: str | None = None
    input_requests: tuple[InputRequest, ...] = field(default_factory=tuple)
    canonical_event_log: str | None = None
    native_event_log: str | None = None
    provider_thread_id: str | None = None
    provider_thread_path: str | None = None
    resume_cursor: dict[str, Any] | None = None


@dataclass(slots=True, frozen=True)
class AgentRunContextSnapshot:
    """Compatibility projection matching the legacy run context shape."""

    branch: str | None = None
    worktree_path: str | None = None
    prompt_used: str | None = None
    skills_loaded: tuple[str, ...] = field(default_factory=tuple)


@dataclass(slots=True, frozen=True)
class AgentRunOutcomeSnapshot:
    """Compatibility projection matching the legacy run outcome shape."""

    exit_code: int | None = None
    summary: str | None = None
    error: str | None = None
    role_result: Any | None = None


@dataclass(slots=True, frozen=True)
class AgentRunRetrySnapshot:
    """Compatibility projection matching the legacy retry shape."""

    retry_count: int = 0
    max_retries: int = 3


@dataclass(slots=True, frozen=True)
class AgentRunSnapshot:
    """Stable read model for one execution run."""

    run_id: str
    agent_id: str
    task_id: str | None
    role: str
    lifecycle: RunLifecycleSnapshot
    runtime: RunRuntimeSnapshot
    workspace: RunWorkspaceSnapshot
    provider: RunProviderSnapshot
    envelope: RunEnvelope
    payload: RoleResultPayload | None
    identity: AgentSnapshotIdentity
    context: AgentRunContextSnapshot
    outcome: AgentRunOutcomeSnapshot
    retry: AgentRunRetrySnapshot
    state: str
    summary: str | None = None
    error: str | None = None


@dataclass(slots=True, frozen=True)
class QuestionAnswerResult:
    """Stable result returned when answering a durable orchestrator question."""

    question: QuestionRecord
    gatekeeper_run: AgentRunSnapshot


@dataclass(slots=True)
class RuntimeExecutionResult:
    """Execution-runtime outcome prior to review/merge handling."""

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
    role_result: RoleResultPayload | None = None


TaskExecutionResult = TaskResult
