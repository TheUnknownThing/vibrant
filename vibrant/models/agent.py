"""Agent lifecycle models for orchestration state."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentType(str, enum.Enum):
    CODE = "code"
    TEST = "test"
    MERGE = "merge"
    GATEKEEPER = "gatekeeper"


class AgentStatus(str, enum.Enum):
    SPAWNING = "spawning"
    CONNECTING = "connecting"
    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"


class AgentProviderMetadata(BaseModel):
    """Provider-specific runtime metadata persisted with the agent record."""

    model_config = ConfigDict(extra="forbid")

    kind: str = "codex"
    transport: str = "app-server-json-rpc"
    runtime_mode: str = "workspace-write"
    provider_thread_id: str | None = None
    resume_cursor: dict[str, Any] | None = None
    thread_path: str | None = None
    rollout_path: str | None = None
    native_event_log: str | None = None
    canonical_event_log: str | None = None


class AgentRecord(BaseModel):
    """Durable record describing one agent process and its provider state."""

    model_config = ConfigDict(extra="forbid")

    TERMINAL_STATUSES: ClassVar[set[AgentStatus]] = {
        AgentStatus.COMPLETED,
        AgentStatus.FAILED,
        AgentStatus.KILLED,
    }
    ALLOWED_TRANSITIONS: ClassVar[dict[AgentStatus, set[AgentStatus]]] = {
        AgentStatus.SPAWNING: {AgentStatus.CONNECTING, AgentStatus.FAILED, AgentStatus.KILLED},
        AgentStatus.CONNECTING: {AgentStatus.RUNNING, AgentStatus.FAILED, AgentStatus.KILLED},
        AgentStatus.RUNNING: {
            AgentStatus.AWAITING_INPUT,
            AgentStatus.COMPLETED,
            AgentStatus.FAILED,
            AgentStatus.KILLED,
        },
        AgentStatus.AWAITING_INPUT: {AgentStatus.RUNNING, AgentStatus.FAILED, AgentStatus.KILLED},
        AgentStatus.COMPLETED: set(),
        AgentStatus.FAILED: set(),
        AgentStatus.KILLED: set(),
    }

    agent_id: str
    task_id: str
    type: AgentType
    status: AgentStatus = AgentStatus.SPAWNING
    pid: int | None = None
    branch: str | None = None
    worktree_path: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None
    provider: AgentProviderMetadata = Field(default_factory=AgentProviderMetadata)
    summary: str | None = None
    prompt_used: str | None = None
    skills_loaded: list[str] = Field(default_factory=list)
    retry_count: int = 0
    max_retries: int = 3
    error: str | None = None

    @model_validator(mode="after")
    def validate_record(self) -> AgentRecord:
        if self.retry_count < 0:
            raise ValueError("retry_count must be >= 0")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.retry_count > self.max_retries:
            raise ValueError("retry_count cannot exceed max_retries")
        if self.started_at and self.finished_at and self.finished_at < self.started_at:
            raise ValueError("finished_at cannot be earlier than started_at")
        return self

    def can_transition_to(self, next_status: AgentStatus) -> bool:
        """Return whether the current agent status may transition to ``next_status``."""

        return next_status in self.ALLOWED_TRANSITIONS[self.status]

    def transition_to(
        self,
        next_status: AgentStatus,
        *,
        finished_at: datetime | None = None,
        exit_code: int | None = None,
        error: str | None = None,
    ) -> None:
        """Transition the agent to a new status, enforcing the lifecycle graph."""

        if not self.can_transition_to(next_status):
            raise ValueError(f"Invalid agent status transition: {self.status.value} -> {next_status.value}")

        self.status = next_status
        if exit_code is not None:
            self.exit_code = exit_code
        if error is not None:
            self.error = error

        if next_status in self.TERMINAL_STATUSES:
            self.finished_at = finished_at or self.finished_at or datetime.now(timezone.utc)
