"""Shared orchestration result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from vibrant.agents.gatekeeper import GatekeeperRunResult
from vibrant.agents.role_results import RoleResultPayload
from vibrant.agents.runtime import InputRequest, NormalizedRunResult, RunState
from vibrant.models.agent import AgentRunRecord
from vibrant.models.task import TaskStatus
from vibrant.orchestrator.execution.git_manager import GitMergeResult
from vibrant.providers.base import CanonicalEvent


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
class OrchestratorAgentSnapshot:
    """Stable orchestrator-facing view of one agent instance and its latest run."""

    identity: AgentSnapshotIdentity
    runtime: AgentSnapshotRuntime
    workspace: AgentSnapshotWorkspace = field(default_factory=AgentSnapshotWorkspace)
    outcome: AgentSnapshotOutcome = field(default_factory=AgentSnapshotOutcome)
    provider: AgentSnapshotProvider = field(default_factory=AgentSnapshotProvider)


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
