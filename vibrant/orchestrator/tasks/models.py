"""Task-centric durable workflow records."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from vibrant.models.task import TaskInfo, TaskStatus


class TaskRunStatus(str, enum.Enum):
    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TaskReviewDecision(str, enum.Enum):
    ACCEPTED = "accepted"
    RETRY = "retry"
    NEEDS_INPUT = "needs_input"
    ESCALATED = "escalated"
    REJECTED = "rejected"

    @classmethod
    def normalize(cls, value: TaskReviewDecision | str) -> TaskReviewDecision:
        if isinstance(value, cls):
            return value

        normalized = str(value).strip().lower()
        mapping = {
            "accept": cls.ACCEPTED,
            "accepted": cls.ACCEPTED,
            "approve": cls.ACCEPTED,
            "approved": cls.ACCEPTED,
            "done": cls.ACCEPTED,
            "retry": cls.RETRY,
            "rejected": cls.REJECTED,
            "reject": cls.REJECTED,
            "needs_changes": cls.RETRY,
            "needs_input": cls.NEEDS_INPUT,
            "awaiting_input": cls.NEEDS_INPUT,
            "escalate": cls.ESCALATED,
            "escalated": cls.ESCALATED,
        }
        try:
            return mapping[normalized]
        except KeyError as exc:
            raise ValueError(f"Unsupported task review decision: {value}") from exc


class TaskRunRecord(BaseModel):
    """Durable record for one execution attempt of a task."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(default_factory=lambda: f"run-{uuid4()}")
    task_id: str
    status: TaskRunStatus = TaskRunStatus.RUNNING
    agent_id: str | None = None
    branch: str | None = None
    worktree_path: str | None = None
    summary: str | None = None
    error: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None

    def finish(
        self,
        status: TaskRunStatus,
        *,
        summary: str | None = None,
        error: str | None = None,
    ) -> None:
        self.status = status
        self.summary = summary
        self.error = error
        self.finished_at = datetime.now(timezone.utc)


class TaskReviewRecord(BaseModel):
    """Durable record for one Gatekeeper review event for a task."""

    model_config = ConfigDict(extra="forbid")

    review_id: str = Field(default_factory=lambda: f"review-{uuid4()}")
    task_id: str
    decision: TaskReviewDecision
    gatekeeper_agent_id: str | None = None
    summary: str | None = None
    reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TaskWorkflowState(BaseModel):
    """Durable workflow state and history for one task."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    branch: str | None = None
    retry_count: int = 0
    max_retries: int = 3
    failure_reason: str | None = None
    last_run_id: str | None = None
    last_review_id: str | None = None
    runs: list[TaskRunRecord] = Field(default_factory=list)
    reviews: list[TaskReviewRecord] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_task(cls, task: TaskInfo) -> Self:
        return cls(
            task_id=task.id,
            status=task.status,
            branch=task.branch,
            retry_count=task.retry_count,
            max_retries=task.max_retries,
            failure_reason=task.failure_reason,
        )

    def sync_from_task(self, task: TaskInfo) -> None:
        self.status = task.status
        self.branch = task.branch
        self.retry_count = task.retry_count
        self.max_retries = task.max_retries
        self.failure_reason = task.failure_reason
        self.touch()

    def apply_to_task(self, task: TaskInfo) -> None:
        task.status = self.status
        task.branch = self.branch
        task.retry_count = self.retry_count
        task.max_retries = self.max_retries
        task.failure_reason = self.failure_reason

    def append_run(self, record: TaskRunRecord) -> None:
        self.runs.append(record)
        self.last_run_id = record.run_id
        self.touch()

    def append_review(self, record: TaskReviewRecord) -> None:
        self.reviews.append(record)
        self.last_review_id = record.review_id
        self.touch()

    def latest_run(self) -> TaskRunRecord | None:
        if self.last_run_id is not None:
            for record in reversed(self.runs):
                if record.run_id == self.last_run_id:
                    return record
        return self.runs[-1] if self.runs else None

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)
