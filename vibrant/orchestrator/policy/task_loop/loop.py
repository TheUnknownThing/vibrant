"""Task loop policy facade."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from vibrant.models.task import TaskInfo

from ...basic.artifacts import build_workflow_snapshot
from ...basic.stores import AgentRunStore, AttemptStore, ConsensusStore, QuestionStore, ReviewTicketStore, RoadmapStore, WorkflowStateStore
from ...basic.workspace import WorkspaceService
from ...types import ReviewResolutionRecord, ReviewTicket, ReviewTicketStatus, TaskResult, WorkflowSnapshot
from . import attempts, dispatch, reviews, task_projection
from .execution import ExecutionCoordinator
from .models import DispatchLease, TaskLoopSnapshot, TaskLoopStage

if TYPE_CHECKING:
    from ..gatekeeper_loop import GatekeeperUserLoop


@dataclass(slots=True)
class TaskLoop:
    """Own the task execution state machine and review decisions."""

    workflow_state_store: WorkflowStateStore
    agent_run_store: AgentRunStore
    attempt_store: AttemptStore
    question_store: QuestionStore
    consensus_store: ConsensusStore
    roadmap_store: RoadmapStore
    review_ticket_store: ReviewTicketStore
    workspace_service: WorkspaceService
    execution: ExecutionCoordinator
    gatekeeper_loop: GatekeeperUserLoop | None = None
    _leased_task_ids: set[str] = field(default_factory=set, repr=False)
    _snapshot: TaskLoopSnapshot = field(default_factory=TaskLoopSnapshot, repr=False)
    _background_attempt_tasks: dict[str, asyncio.Task[TaskResult]] = field(default_factory=dict, repr=False)

    @property
    def state_store(self):
        return self.workflow_state_store

    def workflow_snapshot(self) -> WorkflowSnapshot:
        return build_workflow_snapshot(
            workflow_state_store=self.workflow_state_store,
            agent_run_store=self.agent_run_store,
            question_store=self.question_store,
            attempt_store=self.attempt_store,
        )

    def snapshot(self) -> TaskLoopSnapshot:
        pending_ticket_ids = task_projection.pending_review_ticket_ids(self)
        if pending_ticket_ids and self._snapshot.stage is TaskLoopStage.IDLE:
            self._set_snapshot(
                stage=TaskLoopStage.REVIEW_PENDING,
                active_lease=self._snapshot.active_lease,
                active_attempt_id=self._snapshot.active_attempt_id,
                blocking_reason=self._snapshot.blocking_reason,
            )
        return self._snapshot

    def select_next(self, *, limit: int) -> list[DispatchLease]:
        return dispatch.select_next(self, limit=limit)

    async def run_next_task(self) -> TaskResult | None:
        return await attempts.run_next_task(self)

    async def run_until_blocked(self) -> list[TaskResult]:
        return await attempts.run_until_blocked(self)

    async def pause_active_execution(self):
        return await self.execution.pause_active_attempts()

    async def resume_attempt(self, attempt_id: str):
        return await attempts.resume_attempt(self, attempt_id)

    async def resume_active_execution(self):
        return await attempts.resume_active_attempt(self)

    def track_background_attempt_task(
        self,
        attempt_id: str,
        task: asyncio.Task[TaskResult],
    ) -> None:
        current = self._background_attempt_tasks.get(attempt_id)
        if current is not None and not current.done():
            return
        self._background_attempt_tasks[attempt_id] = task

        def _cleanup(completed: asyncio.Task[TaskResult]) -> None:
            if self._background_attempt_tasks.get(attempt_id) is completed:
                self._background_attempt_tasks.pop(attempt_id, None)

        task.add_done_callback(_cleanup)

    def next_background_attempt_task(self) -> asyncio.Task[TaskResult] | None:
        for attempt_id, task in list(self._background_attempt_tasks.items()):
            if task.done():
                self._background_attempt_tasks.pop(attempt_id, None)
                continue
            return task
        return None

    def get_review_ticket(self, ticket_id: str) -> ReviewTicket | None:
        return reviews.get_review_ticket(self, ticket_id)

    def list_pending_review_tickets(self) -> list[ReviewTicket]:
        return reviews.list_pending_review_tickets(self)

    def list_review_tickets(
        self,
        *,
        task_id: str | None = None,
        status: ReviewTicketStatus | None = None,
    ) -> list[ReviewTicket]:
        return reviews.list_review_tickets(self, task_id=task_id, status=status)

    def accept_review_ticket(self, ticket_id: str) -> ReviewResolutionRecord:
        return reviews.accept_review_ticket(self, ticket_id)

    def retry_review_ticket(
        self,
        ticket_id: str,
        *,
        failure_reason: str,
        prompt_patch: str | None = None,
        acceptance_patch: list[str] | None = None,
    ) -> ReviewResolutionRecord:
        return reviews.retry_review_ticket(
            self,
            ticket_id,
            failure_reason=failure_reason,
            prompt_patch=prompt_patch,
            acceptance_patch=acceptance_patch,
        )

    def escalate_review_ticket(self, ticket_id: str, *, reason: str) -> ReviewResolutionRecord:
        return reviews.escalate_review_ticket(self, ticket_id, reason=reason)

    def restart_failed_task(self, task_id: str) -> TaskInfo:
        return task_projection.restart_failed_task(self, task_id)

    def _set_snapshot(
        self,
        *,
        stage: TaskLoopStage,
        active_lease: DispatchLease | None,
        active_attempt_id: str | None,
        blocking_reason: str | None,
    ) -> None:
        self._snapshot = TaskLoopSnapshot(
            active_lease=active_lease,
            active_attempt_id=active_attempt_id,
            stage=stage,
            pending_review_ticket_ids=task_projection.pending_review_ticket_ids(self),
            blocking_reason=blocking_reason,
        )
