"""Agent lifecycle models for orchestration state."""

from __future__ import annotations

import enum
import re
from datetime import datetime, timezone
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator


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
        """Persist this handle onto provider metadata.

        Legacy mirrored fields are still updated while the refactor is in
        progress so older callers continue to work.
        """

        provider.kind = self.kind
        provider.resume_handle = self
        provider.provider_thread_id = self.thread_id
        provider.thread_path = self.thread_path
        provider.resume_cursor = self.resume_cursor


class AgentProviderMetadata(BaseModel):
    """Provider-specific runtime metadata persisted with the agent record."""

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


class AgentIdentity(BaseModel):
    """Stable identifiers for one agent run."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    task_id: str
    type: AgentType


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

    identity: AgentIdentity
    lifecycle: AgentLifecycle = Field(default_factory=AgentLifecycle)
    context: AgentExecutionContext = Field(default_factory=AgentExecutionContext)
    outcome: AgentOutcome = Field(default_factory=AgentOutcome)
    retry: AgentRetryState = Field(default_factory=AgentRetryState)
    provider: AgentProviderMetadata = Field(default_factory=AgentProviderMetadata)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value

        data = dict(value)
        _move_if_missing(data, "provider_binding", "provider")

        identity_payload = _coerce_model_input(data.pop("identity", None))
        lifecycle_payload = _coerce_model_input(data.pop("lifecycle", None))
        context_payload = _coerce_model_input(data.pop("context", None))
        outcome_payload = _coerce_model_input(data.pop("outcome", None))
        retry_payload = _coerce_model_input(data.pop("retry", None))

        _move_if_missing(data, "agent_kind", "type")
        _move_if_missing(data, "branch_name", "branch")

        _move_if_present(data, identity_payload, "agent_id")
        _move_if_present(data, identity_payload, "task_id")
        _move_if_present(data, identity_payload, "type")

        _move_if_present(data, lifecycle_payload, "status")
        _move_if_present(data, lifecycle_payload, "pid")
        _move_if_present(data, lifecycle_payload, "started_at")
        _move_if_present(data, lifecycle_payload, "finished_at")

        _move_if_present(data, context_payload, "branch")
        _move_if_present(data, context_payload, "worktree_path")
        _move_if_present(data, context_payload, "prompt_used")
        _move_if_present(data, context_payload, "skills_loaded")

        _move_if_present(data, outcome_payload, "exit_code")
        _move_if_present(data, outcome_payload, "summary")
        _move_if_present(data, outcome_payload, "error")

        _move_if_present(data, retry_payload, "retry_count")
        _move_if_present(data, retry_payload, "max_retries")

        if not isinstance(identity_payload.get("task_id"), str) or not identity_payload["task_id"].strip():
            identity_payload["task_id"] = _infer_task_id(identity_payload)

        data.pop("pending_requests", None)
        data.pop("validation_result", None)
        data["identity"] = identity_payload
        data["lifecycle"] = lifecycle_payload
        data["context"] = context_payload
        data["outcome"] = outcome_payload
        data["retry"] = retry_payload
        return data

    @model_serializer(mode="wrap")
    def serialize_record(self, handler: Any, info: Any) -> dict[str, Any]:
        data = handler(self)
        flattened = {
            **data.pop("identity"),
            **data.pop("lifecycle"),
            **data.pop("context"),
            **data.pop("outcome"),
            **data.pop("retry"),
            **data,
        }
        return flattened

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

    @property
    def agent_id(self) -> str:
        return self.identity.agent_id

    @agent_id.setter
    def agent_id(self, value: str) -> None:
        self.identity.agent_id = value

    @property
    def task_id(self) -> str:
        return self.identity.task_id

    @task_id.setter
    def task_id(self, value: str) -> None:
        self.identity.task_id = value

    @property
    def type(self) -> AgentType:
        return self.identity.type

    @type.setter
    def type(self, value: AgentType) -> None:
        self.identity.type = value

    @property
    def status(self) -> AgentStatus:
        return self.lifecycle.status

    @status.setter
    def status(self, value: AgentStatus) -> None:
        self.lifecycle.status = value

    @property
    def pid(self) -> int | None:
        return self.lifecycle.pid

    @pid.setter
    def pid(self, value: int | None) -> None:
        self.lifecycle.pid = value

    @property
    def started_at(self) -> datetime | None:
        return self.lifecycle.started_at

    @started_at.setter
    def started_at(self, value: datetime | None) -> None:
        self.lifecycle.started_at = value

    @property
    def finished_at(self) -> datetime | None:
        return self.lifecycle.finished_at

    @finished_at.setter
    def finished_at(self, value: datetime | None) -> None:
        self.lifecycle.finished_at = value

    @property
    def branch(self) -> str | None:
        return self.context.branch

    @branch.setter
    def branch(self, value: str | None) -> None:
        self.context.branch = value

    @property
    def worktree_path(self) -> str | None:
        return self.context.worktree_path

    @worktree_path.setter
    def worktree_path(self, value: str | None) -> None:
        self.context.worktree_path = value

    @property
    def prompt_used(self) -> str | None:
        return self.context.prompt_used

    @prompt_used.setter
    def prompt_used(self, value: str | None) -> None:
        self.context.prompt_used = value

    @property
    def skills_loaded(self) -> list[str]:
        return self.context.skills_loaded

    @skills_loaded.setter
    def skills_loaded(self, value: list[str]) -> None:
        self.context.skills_loaded = value

    @property
    def exit_code(self) -> int | None:
        return self.outcome.exit_code

    @exit_code.setter
    def exit_code(self, value: int | None) -> None:
        self.outcome.exit_code = value

    @property
    def summary(self) -> str | None:
        return self.outcome.summary

    @summary.setter
    def summary(self, value: str | None) -> None:
        self.outcome.summary = value

    @property
    def error(self) -> str | None:
        return self.outcome.error

    @error.setter
    def error(self, value: str | None) -> None:
        self.outcome.error = value

    @property
    def retry_count(self) -> int:
        return self.retry.retry_count

    @retry_count.setter
    def retry_count(self, value: int) -> None:
        self.retry.retry_count = value

    @property
    def max_retries(self) -> int:
        return self.retry.max_retries

    @max_retries.setter
    def max_retries(self, value: int) -> None:
        self.retry.max_retries = value

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


def _move_if_missing(data: dict[str, Any], old_key: str, new_key: str) -> None:
    if new_key not in data and old_key in data:
        data[new_key] = data.pop(old_key)
    else:
        data.pop(old_key, None)


def _move_if_present(source: dict[str, Any], destination: dict[str, Any], key: str) -> None:
    if key in source and key not in destination:
        destination[key] = source.pop(key)


def _coerce_model_input(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, dict):
        return dict(value)
    raise TypeError(f"Expected mapping or model input, got {type(value).__name__}")


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


def _infer_task_id(data: dict[str, Any]) -> str:
    agent_id = data.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise ValueError("Cannot infer task_id from legacy agent record without a valid agent_id")

    normalized_agent_id = agent_id.removeprefix("agent-")
    agent_type = data.get("type") or data.get("agent_kind")
    if isinstance(agent_type, str) and agent_type.strip().lower() == AgentType.GATEKEEPER.value:
        return re.sub(r"-\d+$", "", normalized_agent_id)
    return normalized_agent_id
