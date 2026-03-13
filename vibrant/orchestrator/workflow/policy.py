"""Workflow policy service."""

from __future__ import annotations

from uuid import uuid4

from vibrant.models.task import TaskStatus
from vibrant.orchestrator.stores.agents import AgentRecordStore
from vibrant.orchestrator.stores.attempts import AttemptStore
from vibrant.orchestrator.stores.questions import QuestionStore
from vibrant.orchestrator.stores.roadmap import RoadmapStore
from vibrant.orchestrator.stores.workflow_state import WorkflowStateStore
from vibrant.orchestrator.types import (
    AttemptCompletion,
    AttemptRecord,
    AttemptStatus,
    DispatchLease,
    GatekeeperLifecycleStatus,
    ReviewTicket,
    TaskState,
    WorkflowSnapshot,
    WorkflowStatus,
    task_state_from_task,
)


class WorkflowPolicyService:
    """Own task dispatch eligibility and task-state transitions."""

    def __init__(
        self,
        *,
        state_store: WorkflowStateStore,
        roadmap_store: RoadmapStore,
        attempt_store: AttemptStore,
        question_store: QuestionStore,
        agent_store: AgentRecordStore,
    ) -> None:
        self.state_store = state_store
        self.roadmap_store = roadmap_store
        self.attempt_store = attempt_store
        self.question_store = question_store
        self.agent_store = agent_store
        self._leased_task_ids: set[str] = set()

    def snapshot(self) -> WorkflowSnapshot:
        state = self.state_store.load()
        active_attempts = self.attempt_store.list_active()
        active_agents = self.agent_store.list_active()
        return WorkflowSnapshot(
            status=state.workflow_status,
            concurrency_limit=state.concurrency_limit,
            gatekeeper=state.gatekeeper_session,
            pending_question_ids=tuple(question.question_id for question in self.question_store.list_pending()),
            active_attempt_ids=tuple(attempt.attempt_id for attempt in active_attempts),
            active_agent_ids=tuple(agent.identity.agent_id for agent in active_agents),
        )

    def select_next(self, *, limit: int) -> list[DispatchLease]:
        snapshot = self.snapshot()
        if snapshot.status is not WorkflowStatus.EXECUTING:
            return []
        if snapshot.pending_question_ids:
            return []
        if snapshot.gatekeeper.lifecycle_state in {
            GatekeeperLifecycleStatus.FAILED,
            GatekeeperLifecycleStatus.AWAITING_USER,
        }:
            return []

        available = snapshot.concurrency_limit - len(snapshot.active_attempt_ids)
        if available <= 0:
            return []

        selected: list[DispatchLease] = []
        document = self.roadmap_store.load()
        accepted = {task.id for task in document.tasks if task.status is TaskStatus.ACCEPTED}

        for task in document.tasks:
            if len(selected) >= min(limit, available):
                break
            if task.id in self._leased_task_ids:
                continue
            if self.attempt_store.get_active_by_task(task.id) is not None:
                continue
            task_state = task_state_from_task(task)
            if task_state not in {TaskState.PENDING, TaskState.READY}:
                continue
            if any(dependency not in accepted for dependency in task.dependencies):
                continue
            if task_state is TaskState.PENDING:
                self.roadmap_store.record_task_state(task.id, TaskState.READY)
            lease = DispatchLease(
                task_id=task.id,
                lease_id=f"lease-{uuid4()}",
                task_definition_version=self.roadmap_store.definition_version(task.id),
                branch_hint=task.branch,
            )
            self._leased_task_ids.add(task.id)
            selected.append(lease)
        return selected

    def on_attempt_started(self, attempt: AttemptRecord) -> WorkflowSnapshot:
        self._leased_task_ids.discard(attempt.task_id)
        self.roadmap_store.record_task_state(
            attempt.task_id,
            TaskState.ACTIVE,
            active_attempt_id=attempt.attempt_id,
        )
        if attempt.status is not AttemptStatus.RUNNING:
            self.attempt_store.update(attempt.attempt_id, status=AttemptStatus.RUNNING)
        return self.snapshot()

    def on_attempt_completed(self, completion: AttemptCompletion) -> WorkflowSnapshot:
        if completion.status == "awaiting_input":
            self.attempt_store.update(completion.attempt_id, status=AttemptStatus.AWAITING_INPUT)
            self.roadmap_store.record_task_state(
                completion.task_id,
                TaskState.BLOCKED,
                active_attempt_id=completion.attempt_id,
                failure_reason="Agent is awaiting input",
            )
            return self.snapshot()

        if completion.status == "cancelled":
            self.attempt_store.update(completion.attempt_id, status=AttemptStatus.CANCELLED)
            self.roadmap_store.record_task_state(
                completion.task_id,
                TaskState.BLOCKED,
                failure_reason=completion.error or "Attempt cancelled",
            )
            return self.snapshot()

        self.attempt_store.update(completion.attempt_id, status=AttemptStatus.REVIEW_PENDING)
        self.roadmap_store.record_task_state(
            completion.task_id,
            TaskState.REVIEW_PENDING,
            active_attempt_id=completion.attempt_id,
            failure_reason=completion.error,
        )
        return self.snapshot()

    def on_review_ticket_created(self, ticket: ReviewTicket) -> WorkflowSnapshot:
        self.attempt_store.update(ticket.attempt_id, status=AttemptStatus.REVIEW_PENDING)
        self.roadmap_store.record_task_state(
            ticket.task_id,
            TaskState.REVIEW_PENDING,
            active_attempt_id=ticket.attempt_id,
        )
        return self.snapshot()

    def mark_task_accepted(self, *, task_id: str, attempt_id: str) -> WorkflowSnapshot:
        self.attempt_store.update(attempt_id, status=AttemptStatus.ACCEPTED)
        self.roadmap_store.record_task_state(task_id, TaskState.ACCEPTED, active_attempt_id=None)
        return self.maybe_complete()

    def requeue_task(self, *, task_id: str, attempt_id: str) -> WorkflowSnapshot:
        self.attempt_store.update(attempt_id, status=AttemptStatus.RETRY_PENDING)
        self.roadmap_store.record_task_state(task_id, TaskState.READY, active_attempt_id=None)
        return self.snapshot()

    def mark_task_blocked(self, *, task_id: str, reason: str) -> WorkflowSnapshot:
        active_attempt = self.attempt_store.get_active_by_task(task_id)
        if active_attempt is not None:
            self.attempt_store.update(active_attempt.attempt_id, status=AttemptStatus.CANCELLED)
        self.roadmap_store.record_task_state(task_id, TaskState.BLOCKED, failure_reason=reason)
        return self.snapshot()

    def mark_task_escalated(self, *, task_id: str, attempt_id: str) -> WorkflowSnapshot:
        self.attempt_store.update(attempt_id, status=AttemptStatus.ESCALATED)
        self.roadmap_store.record_task_state(task_id, TaskState.ESCALATED, active_attempt_id=None)
        return self.snapshot()

    def maybe_complete(self) -> WorkflowSnapshot:
        document = self.roadmap_store.load()
        if document.tasks and all(task.status is TaskStatus.ACCEPTED for task in document.tasks):
            self.state_store.update_workflow_status(WorkflowStatus.COMPLETED)
        return self.snapshot()
