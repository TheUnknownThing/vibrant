"""Agent lifecycle models for orchestration state."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator


class AgentStatus(str, enum.Enum):
    SPAWNING = "spawning"
    CONNECTING = "connecting"
    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"


class ProviderResumeHandle(BaseModel):
    """Durable, serializable provider resume metadata.

    This is persisted state, not a live in-memory runtime handle.
    """

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
        """Return a persisted representation of this handle."""

        return self.model_dump(mode="python")

    @classmethod
    def deserialize(cls, value: object) -> "ProviderResumeHandle":
        """Load a handle from persisted data."""

        return cls.model_validate(value)

    @classmethod
    def from_provider_metadata(cls, provider: "AgentProviderMetadata") -> "ProviderResumeHandle | None":
        """Build a handle from provider metadata, if one exists."""

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
        """Persist this handle onto provider metadata."""

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

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value

        data = dict(value)
        _move_if_missing(data, "provider_name", "kind")
        _move_if_missing(data, "transport_name", "transport")
        _move_if_missing(data, "resume_token", "resume_cursor")
        _move_if_missing(data, "native_event_log_path", "native_event_log")
        _move_if_missing(data, "canonical_event_log_path", "canonical_event_log")

        runtime_mode = data.get("runtime_mode")
        if isinstance(runtime_mode, str):
            data["runtime_mode"] = _normalize_runtime_mode(runtime_mode)

        resume_cursor = data.get("resume_cursor")
        if "thread_path" not in data and isinstance(resume_cursor, dict):
            thread_path = resume_cursor.get("threadPath") or resume_cursor.get("thread_path")
            if isinstance(thread_path, str) and thread_path:
                data["thread_path"] = thread_path

        if "resume_handle" not in data:
            provider_thread_id = data.get("provider_thread_id")
            thread_path = data.get("thread_path")
            if provider_thread_id is not None or thread_path is not None or isinstance(resume_cursor, dict):
                data["resume_handle"] = {
                    "kind": data.get("kind") or "codex",
                    "thread_id": provider_thread_id,
                    "thread_path": thread_path,
                    "resume_cursor": resume_cursor,
                }

        data.pop("owner_agent_id", None)
        data.pop("provider_session_id", None)
        data.pop("runtime_state", None)
        data.pop("last_error", None)
        return data

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
        """Persist a provider resume handle onto this metadata model."""

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

    @field_validator("kind", mode="before")
    @classmethod
    def normalize_kind(cls, value: object) -> object:
        if isinstance(value, str):
            return _normalize_role(value)
        return value

    @field_validator("runtime_mode", mode="before")
    @classmethod
    def normalize_runtime_mode(cls, value: object) -> object:
        if isinstance(value, str):
            return _normalize_runtime_mode(value)
        return value


class AgentInstanceIdentity(BaseModel):
    """Stable identifiers for one logical actor."""

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

    def mark_run_updated(self, run: "AgentRunRecord") -> None:
        if run.identity.agent_id != self.identity.agent_id:
            raise ValueError("run does not belong to this agent instance")
        self.latest_run_id = run.identity.run_id
        self.active_run_id = (
            run.identity.run_id if run.lifecycle.status not in AgentRunRecord.TERMINAL_STATUSES else None
        )
        self.updated_at = datetime.now(timezone.utc)


class AgentRunIdentity(BaseModel):
    """Stable identifiers for one execution run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    agent_id: str
    task_id: str
    role: str

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_identity(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        run_id = data.get("run_id")
        legacy_agent_id = data.get("agent_id")
        if (not isinstance(run_id, str) or not run_id.strip()) and isinstance(legacy_agent_id, str) and legacy_agent_id:
            data["run_id"] = legacy_agent_id
        return data

    @field_validator("role", mode="before")
    @classmethod
    def normalize_role(cls, value: object) -> object:
        if isinstance(value, str):
            return _normalize_role(value)
        return value


class AgentLifecycle(BaseModel):
    """Mutable runtime lifecycle state for one agent run."""

    model_config = ConfigDict(extra="forbid")

    status: AgentStatus = AgentStatus.SPAWNING
    pid: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


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
        """Return whether the current agent status may transition to ``next_status``."""

        return next_status in self.ALLOWED_TRANSITIONS[self.lifecycle.status]

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
            raise ValueError(
                f"Invalid agent status transition: {self.lifecycle.status.value} -> {next_status.value}"
            )

        self.lifecycle.status = next_status
        if exit_code is not None:
            self.outcome.exit_code = exit_code
        if error is not None:
            self.outcome.error = error

        if next_status in self.TERMINAL_STATUSES:
            self.lifecycle.finished_at = finished_at or self.lifecycle.finished_at or datetime.now(timezone.utc)


def _move_if_missing(data: dict[str, Any], old_key: str, new_key: str) -> None:
    if new_key not in data and old_key in data:
        data[new_key] = data.pop(old_key)
    else:
        data.pop(old_key, None)



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
