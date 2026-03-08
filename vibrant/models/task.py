"""Task models for orchestrator planning and dispatch."""

from __future__ import annotations

import enum

from pydantic import BaseModel, Field


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    QUEUED = "queued"
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class TaskLifecycle(str, enum.Enum):
    CREATED = "created"
    READY = "ready"
    ACTIVE = "active"
    DONE = "done"


class TaskInfo(BaseModel):
    id: str
    title: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    lifecycle: TaskLifecycle = TaskLifecycle.CREATED
    branch: str | None = None
    retry_count: int = 0
    max_retries: int = 3
    prompt: str | None = None
    skills: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)

