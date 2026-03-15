"""Task-state projection helpers for the task loop."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from vibrant.models.task import TaskInfo, TaskStatus

from ...types import ReviewTicketStatus, WorkflowStatus
from ..shared.workflow import apply_workflow_status
from .models import DispatchLease, ReviewResolutionCommand, TaskLoopStage, TaskState

if TYPE_CHECKING:
    from .loop import TaskLoop


def record_task_state(
    loop: TaskLoop,
    task_id: str,
    state: TaskState,
    *,
    active_attempt_id: str | None = None,
    failure_reason: str | None = None,
) -> TaskInfo:
    task = require_task(loop, task_id)
    projected = project_task_state(task, state=state, failure_reason=failure_reason)
    return loop.roadmap_store.replace_task(
        projected,
        active_attempt_id=active_attempt_id,
    )


def require_task(loop: TaskLoop, task_id: str) -> TaskInfo:
    task = loop.roadmap_store.get_task(task_id)
    if task is None:
        raise KeyError(f"Task not found: {task_id}")
    return task


def pending_review_ticket_ids(loop: TaskLoop) -> tuple[str, ...]:
    return tuple(ticket.ticket_id for ticket in loop.review_ticket_store.list_pending())


def maybe_complete_workflow(loop: TaskLoop) -> None:
    document = loop.roadmap_store.load()
    if workflow_is_complete(document.tasks):
        apply_workflow_status(
            workflow_state_store=loop.workflow_state_store,
            agent_run_store=loop.agent_run_store,
            consensus_store=loop.consensus_store,
            question_store=loop.question_store,
            attempt_store=loop.attempt_store,
            status=WorkflowStatus.COMPLETED,
        )
        loop._set_snapshot(
            stage=TaskLoopStage.COMPLETED,
            active_lease=None,
            active_attempt_id=None,
            blocking_reason=None,
        )
        return
    if loop._snapshot.stage is TaskLoopStage.BLOCKED:
        return
    loop._set_snapshot(
        stage=TaskLoopStage.IDLE,
        active_lease=None,
        active_attempt_id=None,
        blocking_reason=None,
    )


def set_blocked_if_needed(loop: TaskLoop, reason: str | None) -> None:
    stage = TaskLoopStage.BLOCKED if reason else TaskLoopStage.IDLE
    loop._set_snapshot(
        stage=stage,
        active_lease=None,
        active_attempt_id=None,
        blocking_reason=reason,
    )


def build_dispatch_lease(task: TaskInfo, *, definition_version: int) -> DispatchLease:
    return DispatchLease(
        task_id=task.id,
        lease_id=f"lease-{uuid4()}",
        task_definition_version=definition_version,
        branch_hint=task.branch,
    )


def build_attempt_lease(loop: TaskLoop, attempt) -> DispatchLease:
    task = require_task(loop, attempt.task_id)
    return build_dispatch_lease(
        task,
        definition_version=attempt.task_definition_version,
    )


def review_ticket_status_for_resolution(command: ReviewResolutionCommand) -> ReviewTicketStatus:
    return {
        "accept": ReviewTicketStatus.ACCEPTED,
        "retry": ReviewTicketStatus.RETRY,
        "escalate": ReviewTicketStatus.ESCALATED,
    }[command.decision]


def workflow_is_complete(tasks: list[TaskInfo]) -> bool:
    return bool(tasks) and all(task.status is TaskStatus.ACCEPTED for task in tasks)


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


def task_needs_ready_projection(task: TaskInfo) -> bool:
    return task_state_from_task(task) is TaskState.PENDING


def project_task_state(
    task: TaskInfo,
    *,
    state: TaskState,
    failure_reason: str | None = None,
) -> TaskInfo:
    updated = task.model_copy(deep=True)
    target_status = {
        TaskState.PENDING: TaskStatus.PENDING,
        TaskState.READY: TaskStatus.QUEUED,
        TaskState.ACTIVE: TaskStatus.IN_PROGRESS,
        TaskState.REVIEW_PENDING: TaskStatus.COMPLETED,
        TaskState.BLOCKED: TaskStatus.FAILED,
        TaskState.ACCEPTED: TaskStatus.ACCEPTED,
        TaskState.ESCALATED: TaskStatus.ESCALATED,
    }[state]

    if updated.status is not target_status:
        if updated.can_transition_to(target_status):
            updated.transition_to(target_status, failure_reason=failure_reason)
        else:
            if state is TaskState.READY and updated.status is TaskStatus.FAILED:
                updated.retry_count = min(updated.retry_count + 1, updated.max_retries)
            if state is TaskState.ESCALATED:
                updated.retry_count = max(updated.retry_count, updated.max_retries)
            updated.status = target_status

    if state in {
        TaskState.PENDING,
        TaskState.READY,
        TaskState.ACTIVE,
        TaskState.REVIEW_PENDING,
        TaskState.ACCEPTED,
    }:
        updated.failure_reason = None
    elif state in {TaskState.BLOCKED, TaskState.ESCALATED}:
        updated.failure_reason = failure_reason

    return TaskInfo.model_validate(updated.model_dump(mode="python"))


def requeue_task_for_retry(loop: TaskLoop, task_id: str) -> TaskInfo:
    task = require_task(loop, task_id)
    if task.retry_count >= task.max_retries:
        raise ValueError(f"Task has exhausted retries: {task_id}")
    updated = task.model_copy(deep=True)
    updated.retry_count += 1
    updated.status = TaskStatus.QUEUED
    updated.failure_reason = None
    return loop.roadmap_store.replace_task(updated, active_attempt_id=None)
