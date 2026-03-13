"""Durable task workflow store backed by orchestrator state."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from vibrant.models.task import TaskInfo

from .models import TaskReviewDecision, TaskReviewRecord, TaskRunRecord, TaskRunStatus, TaskWorkflowState

if TYPE_CHECKING:
    from ..state.store import StateStore


class TaskStore:
    """Own the durable task workflow slice within ``state.json``."""

    def __init__(self, *, state_store: StateStore) -> None:
        self.state_store = state_store

    def list_states(self) -> list[TaskWorkflowState]:
        return list(self.state_store.state.tasks.values())

    def get_state(self, task_id: str) -> TaskWorkflowState | None:
        return self.state_store.state.tasks.get(task_id)

    def require_state(self, task_id: str) -> TaskWorkflowState:
        state = self.get_state(task_id)
        if state is None:
            raise KeyError(f"Unknown task id: {task_id}")
        return state

    def sync_tasks(self, tasks: Iterable[TaskInfo], *, prefer_store: bool) -> None:
        task_list = list(tasks)
        seen_ids: set[str] = set()

        for task in task_list:
            seen_ids.add(task.id)
            state = self.state_store.state.tasks.get(task.id)
            if state is None:
                state = TaskWorkflowState.from_task(task)
                self.state_store.state.tasks[task.id] = state
            else:
                state.max_retries = task.max_retries
                if prefer_store:
                    state.apply_to_task(task)
                else:
                    state.sync_from_task(task)

        stale_ids = [task_id for task_id in self.state_store.state.tasks if task_id not in seen_ids]
        for task_id in stale_ids:
            del self.state_store.state.tasks[task_id]

        self.state_store.persist()

    def sync_task(self, task: TaskInfo) -> TaskWorkflowState:
        state = self.state_store.state.tasks.get(task.id)
        if state is None:
            state = TaskWorkflowState.from_task(task)
            self.state_store.state.tasks[task.id] = state
        else:
            state.sync_from_task(task)
        self.state_store.persist()
        return state

    def record_run_started(
        self,
        *,
        task: TaskInfo,
        agent_id: str | None,
        worktree_path: str | None,
    ) -> TaskRunRecord:
        state = self.sync_task(task)
        run = TaskRunRecord(
            task_id=task.id,
            agent_id=agent_id,
            branch=task.branch,
            worktree_path=worktree_path,
        )
        state.append_run(run)
        self.state_store.persist()
        return run

    def record_run_finished(
        self,
        task_id: str,
        *,
        status: TaskRunStatus,
        summary: str | None = None,
        error: str | None = None,
    ) -> TaskRunRecord | None:
        state = self.require_state(task_id)
        run = state.latest_run()
        if run is None:
            return None
        run.finish(status, summary=summary, error=error)
        state.touch()
        self.state_store.persist()
        return run

    def record_review(
        self,
        *,
        task: TaskInfo,
        decision: TaskReviewDecision | str,
        reason: str | None = None,
        summary: str | None = None,
        gatekeeper_agent_id: str | None = None,
    ) -> TaskReviewRecord:
        state = self.sync_task(task)
        record = TaskReviewRecord(
            task_id=task.id,
            decision=TaskReviewDecision.normalize(decision),
            gatekeeper_agent_id=gatekeeper_agent_id,
            summary=summary,
            reason=reason,
        )
        state.append_review(record)
        self.state_store.persist()
        return record
