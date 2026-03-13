"""Task workflow transition rules bound to the task store."""

from __future__ import annotations

from vibrant.models.task import TaskInfo, TaskStatus

from .models import TaskReviewDecision
from .store import TaskStore


class TaskWorkflowService:
    """Apply task lifecycle transitions and keep durable task state in sync."""

    def __init__(self, *, task_store: TaskStore) -> None:
        self.task_store = task_store

    def sync_task(self, task: TaskInfo) -> TaskInfo:
        self.task_store.sync_task(task)
        return task

    def transition_task(
        self,
        task: TaskInfo,
        next_status: TaskStatus,
        *,
        failure_reason: str | None = None,
    ) -> TaskInfo:
        if task.status is not next_status:
            task.transition_to(next_status, failure_reason=failure_reason)
        elif next_status is TaskStatus.FAILED:
            task.failure_reason = failure_reason
        self.task_store.sync_task(task)
        return task

    def fail_task(self, task: TaskInfo, *, failure_reason: str) -> TaskInfo:
        self.transition_task(task, TaskStatus.FAILED, failure_reason=failure_reason)
        if task.can_transition_to(TaskStatus.QUEUED):
            return self.transition_task(task, TaskStatus.QUEUED)
        return self.transition_task(task, TaskStatus.ESCALATED)

    def record_review(
        self,
        task: TaskInfo,
        *,
        decision: TaskReviewDecision | str,
        reason: str | None = None,
        summary: str | None = None,
        gatekeeper_agent_id: str | None = None,
    ) -> None:
        self.task_store.record_review(
            task=task,
            decision=decision,
            reason=reason,
            summary=summary,
            gatekeeper_agent_id=gatekeeper_agent_id,
        )
