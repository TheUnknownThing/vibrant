"""Task dispatch and scheduling helpers."""

from __future__ import annotations

from collections.abc import Iterable

from vibrant.models.task import TaskInfo, TaskStatus

from .workflow import TaskWorkflowService


class TaskDispatcher:
    """Manage task scheduling, dispatch, retries, and escalation."""

    def __init__(
        self,
        tasks: Iterable[TaskInfo] | None = None,
        *,
        concurrency_limit: int = 4,
        workflow_service: TaskWorkflowService | None = None,
    ) -> None:
        if concurrency_limit < 1:
            raise ValueError("concurrency_limit must be >= 1")

        self.concurrency_limit = concurrency_limit
        self.workflow_service = workflow_service
        self._tasks: dict[str, TaskInfo] = {}
        self._task_order: dict[str, int] = {}
        self._next_order = 0
        self._queue: list[str] = []
        self._active: set[str] = set()

        for task in tasks or ():
            self.add_task(task)

    @property
    def tasks(self) -> dict[str, TaskInfo]:
        return self._tasks

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def queued_task_ids(self) -> list[str]:
        self._refresh_ready_queue()
        return list(self._queue)

    @property
    def active_task_ids(self) -> list[str]:
        return sorted(self._active, key=self._task_sort_key)

    def add_task(self, task: TaskInfo) -> None:
        if task.id in self._tasks:
            raise ValueError(f"Task already registered: {task.id}")

        self._tasks[task.id] = task
        self._task_order[task.id] = self._next_order
        self._next_order += 1

        if task.status is TaskStatus.IN_PROGRESS:
            self._active.add(task.id)

        self._schedule_ready_tasks()

    def reconcile_tasks(self, tasks: Iterable[TaskInfo]) -> None:
        task_list = list(tasks)
        next_tasks: dict[str, TaskInfo] = {}
        next_task_order: dict[str, int] = {}
        next_active: set[str] = set()

        for order, task in enumerate(task_list):
            if task.id in next_tasks:
                raise ValueError(f"Duplicate task id: {task.id}")
            next_tasks[task.id] = task
            next_task_order[task.id] = order
            if task.status is TaskStatus.IN_PROGRESS:
                next_active.add(task.id)

        self._tasks = next_tasks
        self._task_order = next_task_order
        self._next_order = len(task_list)
        self._active = next_active
        self._schedule_ready_tasks()

    def get_task(self, task_id: str) -> TaskInfo:
        try:
            return self._tasks[task_id]
        except KeyError as exc:
            raise KeyError(f"Unknown task id: {task_id}") from exc

    def dispatch_ready_tasks(self) -> list[TaskInfo]:
        dispatched: list[TaskInfo] = []
        while True:
            task = self.dispatch_next_task()
            if task is None:
                break
            dispatched.append(task)
        return dispatched

    def dispatch_next_task(self) -> TaskInfo | None:
        self._schedule_ready_tasks()
        if self.active_count >= self.concurrency_limit:
            return None

        while self._queue:
            task_id = self._queue.pop(0)
            task = self.get_task(task_id)
            if task.status is not TaskStatus.QUEUED:
                continue
            if not self._dependencies_satisfied(task):
                continue

            self._transition(task, TaskStatus.IN_PROGRESS)
            self._active.add(task_id)
            return task

        return None

    def mark_completed(self, task_id: str) -> TaskInfo:
        task = self.get_task(task_id)
        self._transition(task, TaskStatus.COMPLETED)
        self._active.discard(task_id)
        self._schedule_ready_tasks()
        return task

    def accept_task(self, task_id: str) -> TaskInfo:
        task = self.get_task(task_id)
        self._transition(task, TaskStatus.ACCEPTED)
        self._schedule_ready_tasks()
        return task

    def fail_task(self, task_id: str, *, failure_reason: str) -> TaskInfo:
        task = self.get_task(task_id)
        self._active.discard(task_id)
        if self.workflow_service is not None:
            self.workflow_service.fail_task(task, failure_reason=failure_reason)
        else:
            task.transition_to(TaskStatus.FAILED, failure_reason=failure_reason)
            if task.can_transition_to(TaskStatus.QUEUED):
                task.transition_to(TaskStatus.QUEUED)
            else:
                task.transition_to(TaskStatus.ESCALATED)
        self._schedule_ready_tasks()
        return task

    def _schedule_ready_tasks(self) -> None:
        for task in self._tasks.values():
            if task.status is TaskStatus.PENDING and self._dependencies_satisfied(task):
                self._transition(task, TaskStatus.QUEUED)
        self._refresh_ready_queue()

    def _refresh_ready_queue(self) -> None:
        self._queue = [
            task.id
            for task in sorted(self._tasks.values(), key=self._task_sort_key)
            if task.status is TaskStatus.QUEUED
            and task.id not in self._active
            and self._dependencies_satisfied(task)
        ]

    def _dependencies_satisfied(self, task: TaskInfo) -> bool:
        for dependency_id in task.dependencies:
            dependency = self._tasks.get(dependency_id)
            if dependency is None:
                return False
            if dependency.status not in {TaskStatus.COMPLETED, TaskStatus.ACCEPTED}:
                return False
        return True

    def _transition(
        self,
        task: TaskInfo,
        next_status: TaskStatus,
        *,
        failure_reason: str | None = None,
    ) -> None:
        if self.workflow_service is not None:
            self.workflow_service.transition_task(task, next_status, failure_reason=failure_reason)
            return
        task.transition_to(next_status, failure_reason=failure_reason)

    def _task_sort_key(self, task: TaskInfo | str) -> tuple[bool, int, int]:
        task_id = task if isinstance(task, str) else task.id
        task_info = self._tasks[task_id]
        priority = task_info.priority if task_info.priority is not None else 0
        return (task_info.priority is None, priority, self._task_order[task_id])
