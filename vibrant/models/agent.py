"""Agent lifecycle models for orchestration state."""

from __future__ import annotations

import enum
import re
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

        data.pop("owner_agent_id", None)
        data.pop("provider_session_id", None)
        data.pop("runtime_state", None)
        data.pop("last_error", None)
        return data


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

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value

        data = dict(value)
        _move_if_missing(data, "agent_kind", "type")
        _move_if_missing(data, "branch_name", "branch")
        _move_if_missing(data, "provider_binding", "provider")

        if not isinstance(data.get("task_id"), str) or not data["task_id"].strip():
            data["task_id"] = _infer_task_id(data)

        data.pop("pending_requests", None)
        data.pop("validation_result", None)
        return data

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


def _move_if_missing(data: dict[str, Any], old_key: str, new_key: str) -> None:
    if new_key not in data and old_key in data:
        data[new_key] = data.pop(old_key)
    else:
        data.pop(old_key, None)


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
