"""Task selection policy for execution dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from vibrant.consensus.roadmap import RoadmapDocument
from vibrant.models.task import TaskStatus

from ...types import WorkflowSnapshot, WorkflowStatus
from .models import DispatchLease, TaskState
from .projections import task_state_from_task


@dataclass(frozen=True, slots=True)
class TaskDispatchSelection:
    leases: tuple[DispatchLease, ...]
    mark_ready_task_ids: tuple[str, ...]
    blocking_reason: str | None = None


def task_execution_block_reason(workflow: WorkflowSnapshot) -> str | None:
    if workflow.pending_question_ids:
        return "Pending user input blocks task execution."
    if workflow.gatekeeper.lifecycle_state.value == "awaiting_user":
        return "Gatekeeper is awaiting input."
    if workflow.gatekeeper.lifecycle_state.value == "failed":
        return workflow.gatekeeper.last_error or "Gatekeeper is in a failed state."
    return None


def execution_slots_available(workflow: WorkflowSnapshot) -> int:
    return workflow.concurrency_limit - len(workflow.active_attempt_ids)


def accepted_task_ids(tasks) -> set[str]:
    return {task.id for task in tasks if task.status is TaskStatus.ACCEPTED}


def task_needs_ready_projection(task) -> bool:
    return task_state_from_task(task) is TaskState.PENDING


def can_dispatch_task(
    task,
    *,
    leased_task_ids: set[str],
    has_active_attempt: bool,
    accepted_task_ids: set[str],
) -> bool:
    if task.id in leased_task_ids or has_active_attempt:
        return False
    task_state = task_state_from_task(task)
    if task_state not in {TaskState.PENDING, TaskState.READY}:
        return False
    return not any(dependency not in accepted_task_ids for dependency in task.dependencies)


def build_dispatch_lease(task, *, definition_version: int) -> DispatchLease:
    return DispatchLease(
        task_id=task.id,
        lease_id=f"lease-{uuid4()}",
        task_definition_version=definition_version,
        branch_hint=task.branch,
    )


def select_dispatch_leases(
    *,
    workflow: WorkflowSnapshot,
    roadmap: RoadmapDocument,
    leased_task_ids: set[str],
    has_active_attempt,
    definition_version_for,
    limit: int,
) -> TaskDispatchSelection:
    if workflow.status is not WorkflowStatus.EXECUTING:
        return TaskDispatchSelection(leases=(), mark_ready_task_ids=())
    reason = task_execution_block_reason(workflow)
    if reason is not None:
        return TaskDispatchSelection(leases=(), mark_ready_task_ids=(), blocking_reason=reason)

    available = execution_slots_available(workflow)
    if available <= 0:
        return TaskDispatchSelection(leases=(), mark_ready_task_ids=(), blocking_reason="No execution slots available.")

    accepted = accepted_task_ids(roadmap.tasks)
    max_selection = min(limit, available)
    leases: list[DispatchLease] = []
    mark_ready_task_ids: list[str] = []
    for task in roadmap.tasks:
        if len(leases) >= max_selection:
            break
        if not can_dispatch_task(
            task,
            leased_task_ids=leased_task_ids,
            has_active_attempt=has_active_attempt(task.id),
            accepted_task_ids=accepted,
        ):
            continue
        if task_needs_ready_projection(task):
            mark_ready_task_ids.append(task.id)
        leases.append(build_dispatch_lease(task, definition_version=definition_version_for(task.id)))
    return TaskDispatchSelection(
        leases=tuple(leases),
        mark_ready_task_ids=tuple(mark_ready_task_ids),
    )
