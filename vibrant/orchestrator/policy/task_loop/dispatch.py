"""Task dispatch policy helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibrant.models.task import TaskInfo, TaskStatus

from ...types import QuestionPriority, WorkflowStatus
from . import task_projection
from .models import DispatchLease, TaskState

if TYPE_CHECKING:
    from .loop import TaskLoop


def select_next(loop: TaskLoop, *, limit: int) -> list[DispatchLease]:
    workflow = loop.workflow_snapshot()
    reason = task_execution_block_reason(loop, workflow)
    if reason is not None:
        task_projection.set_blocked_if_needed(loop, reason)
        return []
    if workflow.status is not WorkflowStatus.EXECUTING:
        task_projection.set_blocked_if_needed(loop, None)
        return []

    available = execution_slots_available(workflow)
    if available <= 0:
        task_projection.set_blocked_if_needed(loop, "No execution slots available.")
        return []

    selected: list[DispatchLease] = []
    document = loop.roadmap_store.load()
    accepted = accepted_task_ids(document.tasks)
    for task in document.tasks:
        if len(selected) >= min(limit, available):
            break
        if not can_dispatch_task(
            task,
            leased_task_ids=loop._leased_task_ids,
            has_active_attempt=loop.attempt_store.get_active_by_task(task.id) is not None,
            accepted_task_ids=accepted,
        ):
            continue
        if task_projection.task_needs_ready_projection(task):
            task_projection.record_task_state(loop, task.id, TaskState.READY)
        lease = task_projection.build_dispatch_lease(
            task,
            definition_version=loop.roadmap_store.definition_version(task.id),
        )
        loop._leased_task_ids.add(task.id)
        selected.append(lease)

    if not selected:
        task_projection.set_blocked_if_needed(loop, None)
    return selected


def task_execution_block_reason(loop: TaskLoop, workflow) -> str | None:
    if has_globally_blocking_question(loop):
        return "Pending user input blocks task execution."
    if workflow.gatekeeper.lifecycle_state.value == "awaiting_user":
        return "Gatekeeper is awaiting input."
    if workflow.gatekeeper.lifecycle_state.value == "failed":
        return workflow.gatekeeper.last_error or "Gatekeeper is in a failed state."
    return None


def has_globally_blocking_question(loop: TaskLoop) -> bool:
    for question in loop.question_store.list_pending():
        if question.priority is not QuestionPriority.BLOCKING:
            continue
        if question.blocking_scope in {"planning", "workflow"}:
            return True
    return False


def execution_slots_available(workflow) -> int:
    return workflow.concurrency_limit - len(workflow.active_attempt_ids)


def accepted_task_ids(tasks: list[TaskInfo]) -> set[str]:
    return {task.id for task in tasks if task.status is TaskStatus.ACCEPTED}


def can_dispatch_task(
    task: TaskInfo,
    *,
    leased_task_ids: set[str],
    has_active_attempt: bool,
    accepted_task_ids: set[str],
) -> bool:
    if task.id in leased_task_ids or has_active_attempt:
        return False
    task_state = task_projection.task_state_from_task(task)
    if task_state not in {TaskState.PENDING, TaskState.READY}:
        return False
    return not any(dependency not in accepted_task_ids for dependency in task.dependencies)
