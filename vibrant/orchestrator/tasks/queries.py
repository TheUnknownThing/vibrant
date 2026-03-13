"""Task-centric read helpers for orchestrator consumers."""

from __future__ import annotations

from .models import TaskWorkflowState
from .store import TaskStore


class TaskQueryService:
    """Small read-model helper over the task store."""

    def __init__(self, *, task_store: TaskStore) -> None:
        self.task_store = task_store

    def by_id(self, task_id: str) -> TaskWorkflowState | None:
        return self.task_store.get_state(task_id)

    def all(self) -> list[TaskWorkflowState]:
        return self.task_store.list_states()
