"""Task models for orchestrator planning and dispatch."""

from __future__ import annotations

import enum
from typing import ClassVar

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    QUEUED = "queued"
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"
    ACCEPTED = "accepted"
    FAILED = "failed"
    ESCALATED = "escalated"


class TaskLifecycle:
    """State-machine rules for task status transitions."""

    ALLOWED_TRANSITIONS: ClassVar[dict[TaskStatus, set[TaskStatus]]] = {
        TaskStatus.PENDING: {TaskStatus.QUEUED},
        TaskStatus.QUEUED: {TaskStatus.IN_PROGRESS},
        TaskStatus.IN_PROGRESS: {TaskStatus.COMPLETED, TaskStatus.FAILED},
        TaskStatus.COMPLETED: {TaskStatus.ACCEPTED, TaskStatus.FAILED},
        TaskStatus.ACCEPTED: set(),
        TaskStatus.FAILED: {TaskStatus.QUEUED, TaskStatus.ESCALATED},
        TaskStatus.ESCALATED: set(),
    }

    @classmethod
    def can_transition(
        cls,
        current: TaskStatus,
        next_status: TaskStatus,
        *,
        retry_count: int,
        max_retries: int,
    ) -> bool:
        if next_status not in cls.ALLOWED_TRANSITIONS[current]:
            return False
        if current == TaskStatus.FAILED and next_status == TaskStatus.QUEUED:
            return retry_count < max_retries
        if current == TaskStatus.FAILED and next_status == TaskStatus.ESCALATED:
            return retry_count >= max_retries
        return True


class TaskInfo(BaseModel):
    """Task definition and runtime status tracked by the orchestrator."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    title: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    branch: str | None = None
    retry_count: int = 0
    max_retries: int = 3
    prompt: str | None = None
    agent_role: str = "code"
    skills: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("dependencies", "depends_on"),
        serialization_alias="dependencies",
    )
    priority: int | None = None
    failure_reason: str | None = None

    @model_validator(mode="after")
    def validate_task(self) -> TaskInfo:
        if self.retry_count < 0:
            raise ValueError("retry_count must be >= 0")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.retry_count > self.max_retries:
            raise ValueError("retry_count cannot exceed max_retries")
        if self.status == TaskStatus.ESCALATED and self.retry_count < self.max_retries:
            raise ValueError("escalated tasks must have exhausted retries")
        return self

    @field_validator("agent_role", mode="before")
    @classmethod
    def normalize_agent_role(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if not normalized:
                raise ValueError("agent_role must not be empty")
            return normalized
        return value

    def can_transition_to(self, next_status: TaskStatus) -> bool:
        """Return whether the task may move to ``next_status``."""

        return TaskLifecycle.can_transition(
            self.status,
            next_status,
            retry_count=self.retry_count,
            max_retries=self.max_retries,
        )

    def transition_to(self, next_status: TaskStatus, *, failure_reason: str | None = None) -> None:
        """Transition the task to a new status, enforcing the lifecycle diagram."""

        if not self.can_transition_to(next_status):
            raise ValueError(f"Invalid task status transition: {self.status.value} -> {next_status.value}")

        if self.status == TaskStatus.FAILED and next_status == TaskStatus.QUEUED:
            self.retry_count += 1
            self.failure_reason = None
        elif next_status == TaskStatus.FAILED:
            self.failure_reason = failure_reason
        elif next_status in {TaskStatus.COMPLETED, TaskStatus.ACCEPTED}:
            self.failure_reason = None

        self.status = next_status
