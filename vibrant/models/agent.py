"""Agent instance and run models for orchestrator state."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator


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


class ProviderResumeHandle(BaseModel):
    """Durable, serializable provider resume metadata."""

    model_config = ConfigDict(extra="forbid")

    kind: str = "codex"
    thread_id: str | None = None
    thread_path: str | None = None
    resume_cursor: dict[str, Any] | None = None

    @property
    def resumable(self) -> bool:
        return self.thread_id is not None

    @property
    def empty(self) -> bool:
        return self.thread_id is None and self.thread_path is None and self.resume_cursor is None

    def serialize(self) -> dict[str, Any]:
        return self.model_dump(mode="python")

    @classmethod
    def deserialize(cls, value: object) -> "ProviderResumeHandle":
        return cls.model_validate(value)

    @classmethod
    def from_provider_metadata(cls, provider: "AgentProviderMetadata") -> "ProviderResumeHandle | None":
        if provider.resume_handle is not None:
            return provider.resume_handle
        if (
            provider.provider_thread_id is None
            and provider.thread_path is None
            and provider.resume_cursor is None
        ):
            return None
        return cls(
            kind=provider.kind,
            thread_id=provider.provider_thread_id,
            thread_path=provider.thread_path,
            resume_cursor=provider.resume_cursor,
        )

    def apply_to_metadata(self, provider: "AgentProviderMetadata") -> None:
        provider.kind = self.kind
        provider.resume_handle = self
        provider.provider_thread_id = self.thread_id
        provider.thread_path = self.thread_path
        provider.resume_cursor = self.resume_cursor


class AgentProviderMetadata(BaseModel):
    """Provider-specific runtime metadata persisted with the run record."""

    model_config = ConfigDict(extra="forbid")

    kind: str = "codex"
    transport: str = "app-server-json-rpc"
    runtime_mode: str = "workspace-write"
    resume_handle: ProviderResumeHandle | None = None
    provider_thread_id: str | None = None
    resume_cursor: dict[str, Any] | None = None
    thread_path: str | None = None
    rollout_path: str | None = None
    native_event_log: str | None = None
    canonical_event_log: str | None = None

    @field_validator("runtime_mode", mode="before")
    @classmethod
    def normalize_runtime_mode(cls, value: object) -> object:
        if isinstance(value, str):
            return _normalize_runtime_mode(value)
        return value

    @model_validator(mode="after")
    def sync_resume_handle(self) -> "AgentProviderMetadata":
        handle = ProviderResumeHandle.from_provider_metadata(self)
        if handle is None:
            self.resume_handle = None
            return self
        handle.apply_to_metadata(self)
        return self

    @model_serializer(mode="wrap")
    def serialize_provider(self, handler: Any, info: Any) -> dict[str, Any]:
        data = handler(self)
        if data.get("resume_handle") is not None:
            data.pop("provider_thread_id", None)
            data.pop("resume_cursor", None)
            data.pop("thread_path", None)
        return data

    def set_resume_handle(self, handle: ProviderResumeHandle | None) -> None:
        if handle is None:
            self.resume_handle = None
            self.provider_thread_id = None
            self.thread_path = None
            self.resume_cursor = None
            return
        handle.apply_to_metadata(self)


class AgentInstanceProviderConfig(BaseModel):
    """Durable provider defaults owned by a stable agent instance."""

    model_config = ConfigDict(extra="forbid")

    kind: str = "codex"
    transport: str = "app-server-json-rpc"
    runtime_mode: str = "workspace-write"

    @field_validator("runtime_mode", mode="before")
    @classmethod
    def normalize_runtime_mode(cls, value: object) -> object:
        if isinstance(value, str):
            return _normalize_runtime_mode(value)
        return value


class AgentInstanceIdentity(BaseModel):
    """Stable identifiers for one logical actor instance."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    role: str

    @field_validator("role", mode="before")
    @classmethod
    def normalize_role(cls, value: object) -> object:
        if isinstance(value, str):
            return _normalize_role(value)
        return value


class AgentInstanceScope(BaseModel):
    """Scope that determines the lifetime of a stable agent instance."""

    model_config = ConfigDict(extra="forbid")

    scope_type: str
    scope_id: str | None = None

    @field_validator("scope_type", mode="before")
    @classmethod
    def normalize_scope_type(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if not normalized:
                raise ValueError("scope_type must not be empty")
            return normalized
        return value


class AgentLifecycle(BaseModel):
    """Mutable runtime lifecycle state for one agent run."""

    model_config = ConfigDict(extra="forbid")

    status: AgentStatus = AgentStatus.SPAWNING
    pid: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    stop_reason: str | None = None


class AgentExecutionContext(BaseModel):
    """Execution context and inputs for one agent run."""

    model_config = ConfigDict(extra="forbid")

    branch: str | None = None
    worktree_path: str | None = None
    prompt_used: str | None = None
    skills_loaded: list[str] = Field(default_factory=list)

class AgentOutcome(BaseModel):
    """Terminal outcome metadata for one agent run."""

    model_config = ConfigDict(extra="forbid")

    exit_code: int | None = None
    summary: str | None = None
    error: str | None = None


class AgentRetryState(BaseModel):
    """Retry counters for one agent run."""

    model_config = ConfigDict(extra="forbid")

    retry_count: int = 0
    max_retries: int = 3


class AgentInstanceRecord(BaseModel):
    """Durable record for one stable agent instance."""

    model_config = ConfigDict(extra="forbid")

    identity: AgentInstanceIdentity
    scope: AgentInstanceScope
    provider: AgentInstanceProviderConfig = Field(default_factory=AgentInstanceProviderConfig)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    latest_run_id: str | None = None
    active_run_id: str | None = None

    def mark_run_updated(self, *, agent_id: str, run_id: str, status: AgentStatus) -> None:
        if agent_id != self.identity.agent_id:
            raise ValueError("run does not belong to this agent instance")
        self.latest_run_id = run_id
        self.active_run_id = run_id if status not in AgentRunRecord.TERMINAL_STATUSES else None
        self.updated_at = datetime.now(timezone.utc)


class AgentRunIdentity(BaseModel):
    """Stable identifiers for one execution run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    agent_id: str
    role: str
    type: AgentType | None = None

    @field_validator("role", mode="before")
    @classmethod
    def normalize_role(cls, value: object) -> object:
        if isinstance(value, str):
            return _normalize_role(value)
        return value

class AgentRunRecord(BaseModel):
    """Durable record describing one run and its provider state."""

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

    identity: AgentRunIdentity
    lifecycle: AgentLifecycle = Field(default_factory=AgentLifecycle)
    context: AgentExecutionContext = Field(default_factory=AgentExecutionContext)
    outcome: AgentOutcome = Field(default_factory=AgentOutcome)
    retry: AgentRetryState = Field(default_factory=AgentRetryState)
    provider: AgentProviderMetadata = Field(default_factory=AgentProviderMetadata)

    @model_validator(mode="after")
    def validate_record(self) -> "AgentRunRecord":
        if self.retry.retry_count < 0:
            raise ValueError("retry_count must be >= 0")
        if self.retry.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.retry.retry_count > self.retry.max_retries:
            raise ValueError("retry_count cannot exceed max_retries")
        if (
            self.lifecycle.started_at
            and self.lifecycle.finished_at
            and self.lifecycle.finished_at < self.lifecycle.started_at
        ):
            raise ValueError("finished_at cannot be earlier than started_at")
        return self

    def can_transition_to(self, next_status: AgentStatus) -> bool:
        return next_status in self.ALLOWED_TRANSITIONS[self.lifecycle.status]

    def transition_to(
        self,
        next_status: AgentStatus,
        *,
        finished_at: datetime | None = None,
        exit_code: int | None = None,
        error: str | None = None,
        stop_reason: str | None = None,
    ) -> None:
        if not self.can_transition_to(next_status):
            raise ValueError(
                f"Invalid agent status transition: {self.lifecycle.status.value} -> {next_status.value}"
            )

        self.lifecycle.status = next_status
        if exit_code is not None:
            self.outcome.exit_code = exit_code
        if error is not None:
            self.outcome.error = error
        if stop_reason is not None:
            self.lifecycle.stop_reason = stop_reason
        elif next_status not in self.TERMINAL_STATUSES:
            self.lifecycle.stop_reason = None

        if next_status in self.TERMINAL_STATUSES:
            self.lifecycle.finished_at = finished_at or self.lifecycle.finished_at or datetime.now(timezone.utc)

# Temporary alias while the run/instance split propagates through lower layers.
AgentRecord = AgentRunRecord


def _normalize_role(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError("role must not be empty")
    return normalized


def _normalize_runtime_mode(value: str) -> str:
    normalized = value.strip().replace("-", "_").lower()
    mapping = {
        "read_only": "read-only",
        "readonly": "read-only",
        "workspace_write": "workspace-write",
        "workspacewrite": "workspace-write",
        "full_access": "danger-full-access",
        "fullaccess": "danger-full-access",
        "danger_full_access": "danger-full-access",
        "dangerfullaccess": "danger-full-access",
    }
    try:
        return mapping[normalized]
    except KeyError as exc:
        raise ValueError(f"Unsupported provider runtime mode: {value!r}") from exc
