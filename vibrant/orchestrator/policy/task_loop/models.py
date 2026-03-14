"""Task-loop policy models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Sequence

from vibrant.models.task import TaskInfo


@dataclass(slots=True)
class DispatchLease:
    task_id: str
    lease_id: str
    task_definition_version: int
    branch_hint: str | None = None


@dataclass(slots=True)
class PreparedTaskExecution:
    lease: DispatchLease
    task: TaskInfo
    prompt: str


class TaskState(str, Enum):
    PENDING = "pending"
    READY = "ready"
    ACTIVE = "active"
    REVIEW_PENDING = "review_pending"
    BLOCKED = "blocked"
    ACCEPTED = "accepted"
    ESCALATED = "escalated"


@dataclass(slots=True)
class ReviewResolutionCommand:
    decision: Literal["accept", "retry", "escalate"]
    failure_reason: str | None = None
    prompt_patch: str | None = None
    acceptance_patch: Sequence[str] | None = None


class TaskLoopStage(str, Enum):
    IDLE = "idle"
    CODING = "coding"
    VALIDATING = "validating"
    REVIEW_PENDING = "review_pending"
    MERGE_PENDING = "merge_pending"
    BLOCKED = "blocked"
    COMPLETED = "completed"


@dataclass(slots=True)
class TaskLoopSnapshot:
    active_lease: DispatchLease | None = None
    active_attempt_id: str | None = None
    stage: TaskLoopStage = TaskLoopStage.IDLE
    pending_review_ticket_ids: tuple[str, ...] = field(default_factory=tuple)
    blocking_reason: str | None = None
