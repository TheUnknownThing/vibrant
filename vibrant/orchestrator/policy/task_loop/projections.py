"""Task-state projection policy."""

from __future__ import annotations

from vibrant.models.task import TaskInfo, TaskStatus

from .models import TaskState


_STATE_TO_STATUS = {
    TaskState.PENDING: TaskStatus.PENDING,
    TaskState.READY: TaskStatus.QUEUED,
    TaskState.ACTIVE: TaskStatus.IN_PROGRESS,
    TaskState.REVIEW_PENDING: TaskStatus.COMPLETED,
    TaskState.BLOCKED: TaskStatus.FAILED,
    TaskState.ACCEPTED: TaskStatus.ACCEPTED,
    TaskState.ESCALATED: TaskStatus.ESCALATED,
}


def task_state_from_task(task: TaskInfo) -> TaskState:
    return {
        TaskStatus.PENDING: TaskState.PENDING,
        TaskStatus.QUEUED: TaskState.READY,
        TaskStatus.IN_PROGRESS: TaskState.ACTIVE,
        TaskStatus.COMPLETED: TaskState.REVIEW_PENDING,
        TaskStatus.ACCEPTED: TaskState.ACCEPTED,
        TaskStatus.FAILED: TaskState.BLOCKED,
        TaskStatus.ESCALATED: TaskState.ESCALATED,
    }[task.status]


def project_task(task: TaskInfo, *, state: TaskState, failure_reason: str | None = None) -> TaskInfo:
    updated = task.model_copy(deep=True)
    target_status = _STATE_TO_STATUS[state]

    if updated.status is not target_status:
        if updated.can_transition_to(target_status):
            updated.transition_to(target_status, failure_reason=failure_reason)
        else:
            if state is TaskState.READY and updated.status is TaskStatus.FAILED:
                updated.retry_count = min(updated.retry_count + 1, updated.max_retries)
            if state is TaskState.ESCALATED:
                updated.retry_count = max(updated.retry_count, updated.max_retries)
            updated.status = target_status

    if state in {TaskState.PENDING, TaskState.READY, TaskState.ACTIVE, TaskState.REVIEW_PENDING, TaskState.ACCEPTED}:
        updated.failure_reason = None
    elif state in {TaskState.BLOCKED, TaskState.ESCALATED}:
        updated.failure_reason = failure_reason

    return TaskInfo.model_validate(updated.model_dump(mode="python"))


def project_task_state(task: TaskInfo, state: TaskState, *, failure_reason: str | None = None) -> TaskInfo:
    return project_task(task, state=state, failure_reason=failure_reason)


def workflow_is_complete(tasks: list[TaskInfo]) -> bool:
    return bool(tasks) and all(task.status is TaskStatus.ACCEPTED for task in tasks)
