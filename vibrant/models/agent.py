"""Agent lifecycle models for orchestration state."""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, Field


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
    kind: str = "codex"
    transport: str = "app-server-json-rpc"
    runtime_mode: str = "workspace-write"
    provider_thread_id: str | None = None
    native_event_log: str | None = None
    canonical_event_log: str | None = None


class AgentRecord(BaseModel):
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

