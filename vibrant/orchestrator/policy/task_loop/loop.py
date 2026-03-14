"""Task loop policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from vibrant.models.task import TaskStatus

from ...basic import ArtifactsCapability, WorkspaceCapability
from ...execution.coordinator import ExecutionCoordinator
from ...types import (
    AttemptStatus,
    DispatchLease,
    MergeOutcome,
    ReviewResolutionCommand,
    ReviewResolutionRecord,
    ReviewTicket,
    TaskResult,
    TaskState,
    ValidationOutcome,
    WorkflowStatus,
    task_state_from_task,
)
from ..models import TaskLoopSnapshot, TaskLoopStage


@dataclass(slots=True)
class TaskLoop:
    """Own the task execution state machine and review decisions."""

    artifacts: ArtifactsCapability
    workspace: WorkspaceCapability
    execution: ExecutionCoordinator
    _leased_task_ids: set[str] = field(default_factory=set, repr=False)
    _snapshot: TaskLoopSnapshot = field(default_factory=TaskLoopSnapshot, repr=False)

    @property
    def state_store(self):
        return self.artifacts.workflow_state_store

    def snapshot(self) -> TaskLoopSnapshot:
        pending_ticket_ids = tuple(ticket.ticket_id for ticket in self.artifacts.review_ticket_store.list_pending())
        if pending_ticket_ids and self._snapshot.stage is TaskLoopStage.IDLE:
            self._snapshot = TaskLoopSnapshot(
                active_lease=self._snapshot.active_lease,
                active_attempt_id=self._snapshot.active_attempt_id,
                stage=TaskLoopStage.REVIEW_PENDING,
                pending_review_ticket_ids=pending_ticket_ids,
                blocking_reason=self._snapshot.blocking_reason,
            )
        return self._snapshot

    def select_next(self, *, limit: int) -> list[DispatchLease]:
        workflow = self.artifacts.workflow_snapshot()
        if workflow.status is not WorkflowStatus.EXECUTING:
            self._set_blocked_if_needed(None)
            return []
        if workflow.pending_question_ids:
            self._set_blocked_if_needed("Pending user input blocks task execution.")
            return []
        if workflow.gatekeeper.lifecycle_state.value in {"failed", "awaiting_user"}:
            reason = "Gatekeeper is awaiting input." if workflow.gatekeeper.lifecycle_state.value == "awaiting_user" else (
                workflow.gatekeeper.last_error or "Gatekeeper is in a failed state."
            )
            self._set_blocked_if_needed(reason)
            return []

        available = workflow.concurrency_limit - len(workflow.active_attempt_ids)
        if available <= 0:
            self._set_blocked_if_needed("No execution slots available.")
            return []

        selected: list[DispatchLease] = []
        document = self.artifacts.roadmap_store.load()
        accepted = {task.id for task in document.tasks if task.status is TaskStatus.ACCEPTED}
        for task in document.tasks:
            if len(selected) >= min(limit, available):
                break
            if task.id in self._leased_task_ids:
                continue
            if self.artifacts.attempt_store.get_active_by_task(task.id) is not None:
                continue
            task_state = task_state_from_task(task)
            if task_state not in {TaskState.PENDING, TaskState.READY}:
                continue
            if any(dependency not in accepted for dependency in task.dependencies):
                continue
            if task_state is TaskState.PENDING:
                self.artifacts.roadmap_store.record_task_state(task.id, TaskState.READY)
            lease = DispatchLease(
                task_id=task.id,
                lease_id=f"lease-{uuid4()}",
                task_definition_version=self.artifacts.roadmap_store.definition_version(task.id),
                branch_hint=task.branch,
            )
            self._leased_task_ids.add(task.id)
            selected.append(lease)

        if not selected:
            self._set_blocked_if_needed(None)
        return selected

    async def run_next_task(self) -> TaskResult | None:
        leases = self.select_next(limit=1)
        if not leases:
            self._maybe_complete_workflow()
            return None

        lease = leases[0]
        self._snapshot = TaskLoopSnapshot(
            active_lease=lease,
            active_attempt_id=None,
            stage=TaskLoopStage.CODING,
            pending_review_ticket_ids=tuple(ticket.ticket_id for ticket in self.artifacts.review_ticket_store.list_pending()),
            blocking_reason=None,
        )

        attempt = await self.execution.start_attempt(lease)
        self._leased_task_ids.discard(attempt.task_id)
        self.artifacts.roadmap_store.record_task_state(
            attempt.task_id,
            TaskState.ACTIVE,
            active_attempt_id=attempt.attempt_id,
        )
        self._snapshot = TaskLoopSnapshot(
            active_lease=lease,
            active_attempt_id=attempt.attempt_id,
            stage=TaskLoopStage.CODING,
            pending_review_ticket_ids=self._snapshot.pending_review_ticket_ids,
            blocking_reason=None,
        )

        completion = await self.execution.await_attempt_completion(attempt.attempt_id)
        if completion.status == "awaiting_input":
            reason = completion.error or "Agent is awaiting input"
            self.artifacts.roadmap_store.record_task_state(
                completion.task_id,
                TaskState.BLOCKED,
                active_attempt_id=completion.attempt_id,
                failure_reason=reason,
            )
            self._snapshot = TaskLoopSnapshot(
                active_lease=lease,
                active_attempt_id=completion.attempt_id,
                stage=TaskLoopStage.BLOCKED,
                pending_review_ticket_ids=self._snapshot.pending_review_ticket_ids,
                blocking_reason=reason,
            )
            return TaskResult(
                task_id=completion.task_id,
                outcome="awaiting_user",
                summary=completion.summary,
                error=completion.error,
            )

        if completion.status in {"failed", "cancelled"}:
            reason = completion.error or "Attempt cancelled"
            self.artifacts.roadmap_store.record_task_state(
                completion.task_id,
                TaskState.BLOCKED,
                active_attempt_id=completion.attempt_id,
                failure_reason=reason,
            )
            self._snapshot = TaskLoopSnapshot(
                active_lease=lease,
                active_attempt_id=completion.attempt_id,
                stage=TaskLoopStage.BLOCKED,
                pending_review_ticket_ids=self._snapshot.pending_review_ticket_ids,
                blocking_reason=reason,
            )
            return TaskResult(
                task_id=completion.task_id,
                outcome="failed",
                summary=completion.summary,
                error=completion.error,
            )

        self._snapshot = TaskLoopSnapshot(
            active_lease=lease,
            active_attempt_id=completion.attempt_id,
            stage=TaskLoopStage.VALIDATING,
            pending_review_ticket_ids=self._snapshot.pending_review_ticket_ids,
            blocking_reason=None,
        )
        self.artifacts.attempt_store.update(completion.attempt_id, status=AttemptStatus.VALIDATING)
        validation = completion.validation or ValidationOutcome(
            status="skipped",
            agent_ids=[],
            summary="Validation not configured yet.",
        )
        self.artifacts.attempt_store.update(completion.attempt_id, status=AttemptStatus.REVIEW_PENDING)
        self.artifacts.roadmap_store.record_task_state(
            completion.task_id,
            TaskState.REVIEW_PENDING,
            active_attempt_id=completion.attempt_id,
            failure_reason=completion.error,
        )
        workspace = self.workspace.get_workspace(task_id=completion.task_id, workspace_id=completion.workspace_ref)
        diff = self.workspace.collect_review_diff(workspace)
        ticket = self._create_review_ticket(completion, diff.path if diff is not None else completion.diff_ref)
        self._snapshot = TaskLoopSnapshot(
            active_lease=lease,
            active_attempt_id=completion.attempt_id,
            stage=TaskLoopStage.REVIEW_PENDING,
            pending_review_ticket_ids=tuple(item.ticket_id for item in self.artifacts.review_ticket_store.list_pending()),
            blocking_reason=None,
        )
        return TaskResult(
            task_id=completion.task_id,
            outcome="review_pending",
            summary=validation.summary or completion.summary,
            error=completion.error,
            worktree_path=workspace.path,
        )

    async def run_until_blocked(self) -> list[TaskResult]:
        results: list[TaskResult] = []
        while True:
            result = await self.run_next_task()
            if result is None:
                break
            results.append(result)
            if result.outcome in {"awaiting_user", "review_pending", "failed"}:
                break
        return results

    def get_review_ticket(self, ticket_id: str) -> ReviewTicket | None:
        return self.artifacts.review_ticket_store.get(ticket_id)

    def list_pending_review_tickets(self) -> list[ReviewTicket]:
        return self.artifacts.review_ticket_store.list_pending()

    def accept_review_ticket(self, ticket_id: str) -> ReviewResolutionRecord:
        return self._resolve_review_ticket(ticket_id, ReviewResolutionCommand(decision="accept"))

    def retry_review_ticket(
        self,
        ticket_id: str,
        *,
        failure_reason: str,
        prompt_patch: str | None = None,
        acceptance_patch: list[str] | None = None,
    ) -> ReviewResolutionRecord:
        if prompt_patch is not None or acceptance_patch is not None:
            ticket = self._require_ticket(ticket_id)
            self.artifacts.roadmap_store.update_task_definition(
                ticket.task_id,
                prompt=prompt_patch,
                acceptance_criteria=acceptance_patch,
            )
        return self._resolve_review_ticket(
            ticket_id,
            ReviewResolutionCommand(
                decision="retry",
                failure_reason=failure_reason,
                prompt_patch=prompt_patch,
                acceptance_patch=acceptance_patch,
            ),
        )

    def escalate_review_ticket(self, ticket_id: str, *, reason: str) -> ReviewResolutionRecord:
        return self._resolve_review_ticket(
            ticket_id,
            ReviewResolutionCommand(decision="escalate", failure_reason=reason),
        )

    def review_task_outcome(
        self,
        task_id: str,
        *,
        decision: str,
        failure_reason: str | None = None,
    ):
        ticket = next((item for item in self.artifacts.review_ticket_store.list_pending() if item.task_id == task_id), None)
        if ticket is None:
            raise KeyError(f"No pending review ticket for task {task_id}")
        normalized = decision.strip().lower()
        if normalized in {"accept", "accepted", "approve", "approved"}:
            self.accept_review_ticket(ticket.ticket_id)
        elif normalized in {"retry", "reject", "needs_changes", "rejected"}:
            self.retry_review_ticket(ticket.ticket_id, failure_reason=failure_reason or "Retry requested")
        else:
            self.escalate_review_ticket(ticket.ticket_id, reason=failure_reason or "Task escalated")
        task = self.artifacts.roadmap_store.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found after review: {task_id}")
        return task

    def mark_task_for_retry(
        self,
        task_id: str,
        *,
        failure_reason: str,
        prompt: str | None = None,
        acceptance_criteria: list[str] | None = None,
    ):
        ticket = next((item for item in self.artifacts.review_ticket_store.list_pending() if item.task_id == task_id), None)
        if ticket is not None:
            self.retry_review_ticket(
                ticket.ticket_id,
                failure_reason=failure_reason,
                prompt_patch=prompt,
                acceptance_patch=acceptance_criteria,
            )
        else:
            if prompt is not None or acceptance_criteria is not None:
                self.artifacts.roadmap_store.update_task_definition(
                    task_id,
                    prompt=prompt,
                    acceptance_criteria=acceptance_criteria,
                )
            self.artifacts.roadmap_store.record_task_state(
                task_id,
                TaskState.READY,
                active_attempt_id=None,
                failure_reason=failure_reason,
            )
        task = self.artifacts.roadmap_store.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found after retry mark: {task_id}")
        return task

    def _resolve_review_ticket(
        self,
        ticket_id: str,
        command: ReviewResolutionCommand,
    ) -> ReviewResolutionRecord:
        ticket = self._require_ticket(ticket_id)
        merge_outcome: MergeOutcome | None = None
        follow_up_ticket_id: str | None = None

        if command.decision == "accept":
            attempt = self.artifacts.attempt_store.get(ticket.attempt_id)
            if attempt is None:
                raise KeyError(f"Attempt not found for review ticket: {ticket.attempt_id}")
            workspace = self.workspace.get_workspace(task_id=ticket.task_id, workspace_id=attempt.workspace_id)
            self.artifacts.attempt_store.update(ticket.attempt_id, status=AttemptStatus.MERGE_PENDING)
            self._snapshot = TaskLoopSnapshot(
                active_lease=self._snapshot.active_lease,
                active_attempt_id=ticket.attempt_id,
                stage=TaskLoopStage.MERGE_PENDING,
                pending_review_ticket_ids=tuple(item.ticket_id for item in self.artifacts.review_ticket_store.list_pending()),
                blocking_reason=None,
            )
            merge_outcome = self.workspace.merge_task_result(workspace)
            if merge_outcome.status == "merged":
                self.artifacts.attempt_store.update(ticket.attempt_id, status=AttemptStatus.ACCEPTED)
                self.artifacts.roadmap_store.record_task_state(
                    ticket.task_id,
                    TaskState.ACCEPTED,
                    active_attempt_id=None,
                )
                self._maybe_complete_workflow()
            else:
                self.artifacts.attempt_store.update(ticket.attempt_id, status=AttemptStatus.REVIEW_PENDING)
                follow_up = self.artifacts.review_ticket_store.create(
                    task_id=ticket.task_id,
                    attempt_id=ticket.attempt_id,
                    agent_id=ticket.agent_id,
                    review_kind="merge_failure",
                    conversation_id=ticket.conversation_id,
                    summary=merge_outcome.message,
                    diff_ref=ticket.diff_ref,
                )
                follow_up_ticket_id = follow_up.ticket_id
                self._snapshot = TaskLoopSnapshot(
                    active_lease=self._snapshot.active_lease,
                    active_attempt_id=ticket.attempt_id,
                    stage=TaskLoopStage.REVIEW_PENDING,
                    pending_review_ticket_ids=tuple(item.ticket_id for item in self.artifacts.review_ticket_store.list_pending()),
                    blocking_reason=None,
                )
        elif command.decision == "retry":
            self.artifacts.attempt_store.update(ticket.attempt_id, status=AttemptStatus.RETRY_PENDING)
            self.artifacts.roadmap_store.record_task_state(
                ticket.task_id,
                TaskState.READY,
                active_attempt_id=None,
            )
            self._snapshot = TaskLoopSnapshot(
                active_lease=None,
                active_attempt_id=None,
                stage=TaskLoopStage.IDLE,
                pending_review_ticket_ids=tuple(
                    item.ticket_id
                    for item in self.artifacts.review_ticket_store.list_pending()
                    if item.ticket_id != ticket_id
                ),
                blocking_reason=None,
            )
        else:
            self.artifacts.attempt_store.update(ticket.attempt_id, status=AttemptStatus.ESCALATED)
            self.artifacts.roadmap_store.record_task_state(
                ticket.task_id,
                TaskState.ESCALATED,
                active_attempt_id=None,
            )
            self._snapshot = TaskLoopSnapshot(
                active_lease=None,
                active_attempt_id=None,
                stage=TaskLoopStage.BLOCKED,
                pending_review_ticket_ids=tuple(
                    item.ticket_id
                    for item in self.artifacts.review_ticket_store.list_pending()
                    if item.ticket_id != ticket_id
                ),
                blocking_reason=command.failure_reason or "Task escalated",
            )

        resolution = ReviewResolutionRecord(
            ticket_id=ticket.ticket_id,
            task_id=ticket.task_id,
            attempt_id=ticket.attempt_id,
            decision=command.decision,
            applied=True,
            merge_outcome=merge_outcome,
            follow_up_ticket_id=follow_up_ticket_id,
        )
        self.artifacts.review_ticket_store.resolve(ticket_id, resolution, reason=command.failure_reason)
        if command.decision == "accept" and merge_outcome is not None and merge_outcome.status == "merged":
            next_stage = TaskLoopStage.COMPLETED if self.artifacts.workflow_state_store.load().workflow_status is WorkflowStatus.COMPLETED else TaskLoopStage.IDLE
            self._snapshot = TaskLoopSnapshot(
                active_lease=None,
                active_attempt_id=None,
                stage=next_stage,
                pending_review_ticket_ids=tuple(item.ticket_id for item in self.artifacts.review_ticket_store.list_pending()),
                blocking_reason=None,
            )
        return resolution

    def _require_ticket(self, ticket_id: str) -> ReviewTicket:
        ticket = self.artifacts.review_ticket_store.get(ticket_id)
        if ticket is None:
            raise KeyError(f"Review ticket not found: {ticket_id}")
        return ticket

    def _create_review_ticket(self, completion, diff_ref: str | None) -> ReviewTicket:
        return self.artifacts.review_ticket_store.create(
            task_id=completion.task_id,
            attempt_id=completion.attempt_id,
            agent_id=completion.code_agent_id,
            review_kind="task_result",
            conversation_id=completion.conversation_ref,
            summary=completion.summary,
            diff_ref=diff_ref,
        )

    def _maybe_complete_workflow(self) -> None:
        document = self.artifacts.roadmap_store.load()
        if document.tasks and all(task.status is TaskStatus.ACCEPTED for task in document.tasks):
            self.artifacts.workflow_state_store.update_workflow_status(WorkflowStatus.COMPLETED)
            self._snapshot = TaskLoopSnapshot(
                active_lease=None,
                active_attempt_id=None,
                stage=TaskLoopStage.COMPLETED,
                pending_review_ticket_ids=tuple(item.ticket_id for item in self.artifacts.review_ticket_store.list_pending()),
                blocking_reason=None,
            )
            return
        if self._snapshot.stage is TaskLoopStage.BLOCKED:
            return
        self._snapshot = TaskLoopSnapshot(
            active_lease=None,
            active_attempt_id=None,
            stage=TaskLoopStage.IDLE,
            pending_review_ticket_ids=tuple(item.ticket_id for item in self.artifacts.review_ticket_store.list_pending()),
            blocking_reason=None,
        )

    def _set_blocked_if_needed(self, reason: str | None) -> None:
        stage = TaskLoopStage.BLOCKED if reason else TaskLoopStage.IDLE
        self._snapshot = TaskLoopSnapshot(
            active_lease=None,
            active_attempt_id=None,
            stage=stage,
            pending_review_ticket_ids=tuple(item.ticket_id for item in self.artifacts.review_ticket_store.list_pending()),
            blocking_reason=reason,
        )
