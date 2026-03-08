"""Task queue and dispatch helpers."""

from __future__ import annotations

from collections.abc import Iterable

from vibrant.models.task import TaskInfo, TaskStatus


class TaskDispatcher:
    """Manage task scheduling, dispatch, retries, and escalation."""

    def __init__(self, tasks: Iterable[TaskInfo] | None = None, *, concurrency_limit: int = 4) -> None:
        if concurrency_limit < 1:
            raise ValueError("concurrency_limit must be >= 1")

        self.concurrency_limit = concurrency_limit
        self._tasks: dict[str, TaskInfo] = {}
        self._task_order: dict[str, int] = {}
        self._next_order = 0
        self._queue: list[str] = []
        self._active: set[str] = set()

        for task in tasks or ():
            self.add_task(task)

    @property
    def tasks(self) -> dict[str, TaskInfo]:
        """Return the tracked tasks keyed by task id."""

        return self._tasks

    @property
    def active_count(self) -> int:
        """Return the number of tasks currently in progress."""

        return len(self._active)

    @property
    def queued_task_ids(self) -> list[str]:
        """Return queued task ids in dispatch order."""

        self._refresh_ready_queue()
        return list(self._queue)

    @property
    def active_task_ids(self) -> list[str]:
        """Return active task ids in stable registration order."""

        return sorted(self._active, key=self._task_sort_key)

    def add_task(self, task: TaskInfo) -> None:
        """Register a task with the dispatcher."""

        if task.id in self._tasks:
            raise ValueError(f"Task already registered: {task.id}")

        self._tasks[task.id] = task
        self._task_order[task.id] = self._next_order
        self._next_order += 1

        if task.status is TaskStatus.IN_PROGRESS:
            self._active.add(task.id)

        self._schedule_ready_tasks()

    def get_task(self, task_id: str) -> TaskInfo:
        """Return a tracked task by id."""

        try:
            return self._tasks[task_id]
        except KeyError as exc:
            raise KeyError(f"Unknown task id: {task_id}") from exc

    def dispatch_ready_tasks(self) -> list[TaskInfo]:
        """Dispatch queued tasks up to the concurrency limit."""

        self._schedule_ready_tasks()
        dispatched: list[TaskInfo] = []

        while self._queue and self.active_count < self.concurrency_limit:
            task_id = self._queue.pop(0)
            task = self.get_task(task_id)
            if task.status is not TaskStatus.QUEUED:
                continue
            if not self._dependencies_satisfied(task):
                continue

            task.transition_to(TaskStatus.IN_PROGRESS)
            self._active.add(task_id)
            dispatched.append(task)

        return dispatched

    def mark_completed(self, task_id: str) -> TaskInfo:
        """Mark an in-progress task as completed."""

        task = self.get_task(task_id)
        task.transition_to(TaskStatus.COMPLETED)
        self._active.discard(task_id)
        self._schedule_ready_tasks()
        return task

    def accept_task(self, task_id: str) -> TaskInfo:
        """Mark a completed task as accepted by the gatekeeper."""

        task = self.get_task(task_id)
        task.transition_to(TaskStatus.ACCEPTED)
        self._schedule_ready_tasks()
        return task

    def fail_task(self, task_id: str, *, failure_reason: str) -> TaskInfo:
        """Fail a task and either re-queue it or escalate it."""

        task = self.get_task(task_id)
        task.transition_to(TaskStatus.FAILED, failure_reason=failure_reason)
        self._active.discard(task_id)

        if task.can_transition_to(TaskStatus.QUEUED):
            task.transition_to(TaskStatus.QUEUED)
        else:
            task.transition_to(TaskStatus.ESCALATED)

        self._schedule_ready_tasks()
        return task

    def _schedule_ready_tasks(self) -> None:
        for task in self._tasks.values():
            if task.status is TaskStatus.PENDING and self._dependencies_satisfied(task):
                task.transition_to(TaskStatus.QUEUED)

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

    def _task_sort_key(self, task: TaskInfo | str) -> tuple[bool, int, int]:
        task_id = task if isinstance(task, str) else task.id
        task_info = self._tasks[task_id]
        priority = task_info.priority if task_info.priority is not None else 0
        return (task_info.priority is None, priority, self._task_order[task_id])
