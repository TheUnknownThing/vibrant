"""Shared orchestration result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from vibrant.agents.runtime import InputRequest, NormalizedRunResult, RunState
from vibrant.gatekeeper import GatekeeperRunResult
from vibrant.models.agent import AgentRecord
from vibrant.models.task import TaskStatus
from vibrant.orchestrator.git_manager import GitMergeResult
from vibrant.providers.base import CanonicalEvent


@dataclass(slots=True)
class CodeAgentLifecycleResult:
    """Structured outcome for one code-agent execution attempt."""

    task_id: str | None
    outcome: str
    task_status: TaskStatus | None = None
    agent_record: AgentRecord | None = None
    gatekeeper_result: GatekeeperRunResult | None = None
    merge_result: GitMergeResult | None = None
    events: list[CanonicalEvent] = field(default_factory=list)
    summary: str | None = None
    error: str | None = None
    worktree_path: str | None = None


@dataclass(slots=True)
class RuntimeExecutionResult:
    """Execution-runtime outcome prior to review/merge handling."""

    agent_record: AgentRecord
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
