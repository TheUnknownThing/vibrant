"""Task loop policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from vibrant.models.task import TaskInfo, TaskStatus

from ...basic.artifacts import build_workflow_snapshot
from ...basic.stores import AgentRunStore, AttemptStore, ConsensusStore, QuestionStore, ReviewTicketStore, RoadmapStore, WorkflowStateStore
from ...basic.workspace import WorkspaceService
from ...types import (
    AttemptRecord,
    AttemptStatus,
    MergeOutcome,
    ReviewResolutionRecord,
    ReviewTicket,
    ReviewTicketStatus,
    TaskResult,
    ValidationOutcome,
    WorkflowSnapshot,
    WorkflowStatus,
)
from ..shared.workflow import apply_workflow_status
from .execution import ExecutionCoordinator
from .models import (
    DispatchLease,
    ReviewResolutionCommand,
    TaskLoopSnapshot,
    TaskLoopStage,
    TaskState,
    WORKER_INPUT_UNSUPPORTED_ERROR,
)
from .prompting import prepare_task_execution, retry_definition_patch


@dataclass(slots=True)
class _AttemptRecoveryResult:
    attempt: AttemptRecord | None = None
    task_result: TaskResult | None = None


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
    _leased_task_ids: set[str] = field(default_factory=set, repr=False)
    _snapshot: TaskLoopSnapshot = field(default_factory=TaskLoopSnapshot, repr=False)

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
        pending_ticket_ids = self._pending_review_ticket_ids()
        if pending_ticket_ids and self._snapshot.stage is TaskLoopStage.IDLE:
            self._set_snapshot(
                stage=TaskLoopStage.REVIEW_PENDING,
                active_lease=self._snapshot.active_lease,
                active_attempt_id=self._snapshot.active_attempt_id,
                blocking_reason=self._snapshot.blocking_reason,
            )
        return self._snapshot

    def select_next(self, *, limit: int) -> list[DispatchLease]:
        workflow = self.workflow_snapshot()
        reason = self._task_execution_block_reason(workflow)
        if reason is not None:
            self._set_blocked_if_needed(reason)
            return []
        if workflow.status is not WorkflowStatus.EXECUTING:
            self._set_blocked_if_needed(None)
            return []

        available = self._execution_slots_available(workflow)
        if available <= 0:
            self._set_blocked_if_needed("No execution slots available.")
            return []

        selected: list[DispatchLease] = []
        document = self.roadmap_store.load()
        accepted = self._accepted_task_ids(document.tasks)
        for task in document.tasks:
            if len(selected) >= min(limit, available):
                break
            if not self._can_dispatch_task(
                task,
                leased_task_ids=self._leased_task_ids,
                has_active_attempt=self.attempt_store.get_active_by_task(task.id) is not None,
                accepted_task_ids=accepted,
            ):
                continue
            if self._task_needs_ready_projection(task):
                self._record_task_state(task.id, TaskState.READY)
            lease = self._build_dispatch_lease(
                task,
                definition_version=self.roadmap_store.definition_version(task.id),
            )
            self._leased_task_ids.add(task.id)
            selected.append(lease)

        if not selected:
            self._set_blocked_if_needed(None)
        return selected

    async def run_next_task(self) -> TaskResult | None:
        recovery = await self._recover_active_attempt()
        if recovery.task_result is not None:
            return recovery.task_result
        if recovery.attempt is not None:
            lease = self._build_attempt_lease(recovery.attempt)
            return await self._await_attempt_result(lease, recovery.attempt)

        leases = self.select_next(limit=1)
        if not leases:
            self._maybe_complete_workflow()
            return None

        lease = leases[0]
        self._set_snapshot(
            stage=TaskLoopStage.CODING,
            active_lease=lease,
            active_attempt_id=None,
            blocking_reason=None,
        )

        prepared = prepare_task_execution(
            lease=lease,
            roadmap_store=self.roadmap_store,
            consensus_store=self.consensus_store,
            project_name=self.consensus_store.project_name,
        )
        try:
            attempt = await self.execution.start_attempt(prepared)
        except Exception as exc:
            self._leased_task_ids.discard(lease.task_id)
            reason = str(exc)
            self._record_task_state(
                lease.task_id,
                TaskState.BLOCKED,
                active_attempt_id=None,
                failure_reason=reason,
            )
            self._set_snapshot(
                stage=TaskLoopStage.BLOCKED,
                active_lease=lease,
                active_attempt_id=None,
                blocking_reason=reason,
            )
            return TaskResult(task_id=lease.task_id, outcome="failed", error=reason)

        self._leased_task_ids.discard(attempt.task_id)
        self._record_task_state(attempt.task_id, TaskState.ACTIVE, active_attempt_id=attempt.attempt_id)
        self._set_snapshot(
            stage=TaskLoopStage.CODING,
            active_lease=lease,
            active_attempt_id=attempt.attempt_id,
            blocking_reason=None,
        )

        return await self._await_attempt_result(lease, attempt)

    async def _await_attempt_result(self, lease: DispatchLease, attempt) -> TaskResult:
        try:
            completion = await self.execution.await_attempt_completion(attempt.attempt_id)
        except Exception as exc:
            reason = str(exc)
            self.attempt_store.update(attempt.attempt_id, status=AttemptStatus.FAILED)
            self._record_task_state(
                attempt.task_id,
                TaskState.BLOCKED,
                active_attempt_id=attempt.attempt_id,
                failure_reason=reason,
            )
            self._set_snapshot(
                stage=TaskLoopStage.BLOCKED,
                active_lease=lease,
                active_attempt_id=attempt.attempt_id,
                blocking_reason=reason,
            )
            return TaskResult(task_id=attempt.task_id, outcome="failed", error=reason)

        return self._consume_attempt_completion(lease, completion)

    def _consume_attempt_completion(self, lease: DispatchLease, completion: AttemptCompletion) -> TaskResult:
        if completion.status == "awaiting_input":
            reason = completion.error or WORKER_INPUT_UNSUPPORTED_ERROR
            self.attempt_store.update(completion.attempt_id, status=AttemptStatus.FAILED)
            self._record_task_state(
                completion.task_id,
                TaskState.BLOCKED,
                active_attempt_id=completion.attempt_id,
                failure_reason=reason,
            )
            self._set_snapshot(
                stage=TaskLoopStage.BLOCKED,
                active_lease=lease,
                active_attempt_id=completion.attempt_id,
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
            terminal_status = (
                AttemptStatus.CANCELLED if completion.status == "cancelled" else AttemptStatus.FAILED
            )
            self.attempt_store.update(completion.attempt_id, status=terminal_status)
            self._record_task_state(
                completion.task_id,
                TaskState.BLOCKED,
                active_attempt_id=completion.attempt_id,
                failure_reason=reason,
            )
            self._set_snapshot(
                stage=TaskLoopStage.BLOCKED,
                active_lease=lease,
                active_attempt_id=completion.attempt_id,
                blocking_reason=reason,
            )
            return TaskResult(
                task_id=completion.task_id,
                outcome="failed",
                summary=completion.summary,
                error=completion.error,
            )

        self._set_snapshot(
            stage=TaskLoopStage.VALIDATING,
            active_lease=lease,
            active_attempt_id=completion.attempt_id,
            blocking_reason=None,
        )
        self.attempt_store.update(completion.attempt_id, status=AttemptStatus.VALIDATING)
        validation = completion.validation or ValidationOutcome(
            status="skipped",
            run_ids=[],
            summary="Validation not configured yet.",
        )
        self.attempt_store.update(completion.attempt_id, status=AttemptStatus.REVIEW_PENDING)
        self._record_task_state(
            completion.task_id,
            TaskState.REVIEW_PENDING,
            active_attempt_id=completion.attempt_id,
            failure_reason=completion.error,
        )
        workspace = self.workspace_service.get_workspace(task_id=completion.task_id, workspace_id=completion.workspace_ref)
        diff = self.workspace_service.collect_review_diff(workspace)
        self._create_review_ticket(completion, diff.path if diff is not None else completion.diff_ref)
        self._set_snapshot(
            stage=TaskLoopStage.REVIEW_PENDING,
            active_lease=lease,
            active_attempt_id=completion.attempt_id,
            blocking_reason=None,
        )
        return TaskResult(
            task_id=completion.task_id,
            outcome="review_pending",
            summary=validation.summary or completion.summary,
            error=completion.error,
            worktree_path=workspace.path,
        )

    async def _recover_active_attempt(self) -> _AttemptRecoveryResult:
        workflow = self.workflow_snapshot()
        reason = self._task_execution_block_reason(workflow)
        if reason is not None:
            self._set_blocked_if_needed(reason)
            return _AttemptRecoveryResult()
        if workflow.status is not WorkflowStatus.EXECUTING:
            self._set_blocked_if_needed(None)
            return _AttemptRecoveryResult()

        list_selector = getattr(self.execution, "list_active_attempt_executions", None)
        active_sessions = list_selector() if callable(list_selector) else []
        durable_completion_getter = getattr(self.execution, "durable_attempt_completion", None)
        if callable(durable_completion_getter):
            for session in active_sessions:
                durable_completion = durable_completion_getter(session.attempt_id)
                if durable_completion is None:
                    continue
                attempt = self.attempt_store.get(session.attempt_id)
                if attempt is None:
                    continue
                lease = self._build_attempt_lease(attempt)
                return _AttemptRecoveryResult(
                    task_result=self._consume_attempt_completion(lease, durable_completion),
                )

        recover_selector = getattr(self.execution, "next_attempt_to_recover", None)
        session = recover_selector() if callable(recover_selector) else None
        if session is None:
            return _AttemptRecoveryResult()
        attempt = self.attempt_store.get(session.attempt_id)
        if attempt is None:
            return _AttemptRecoveryResult()
        lease = self._build_attempt_lease(attempt)
        self._set_snapshot(
            stage=TaskLoopStage.CODING,
            active_lease=lease,
            active_attempt_id=attempt.attempt_id,
            blocking_reason=None,
        )
        prepared = prepare_task_execution(
            lease=lease,
            roadmap_store=self.roadmap_store,
            consensus_store=self.consensus_store,
            project_name=self.consensus_store.project_name,
        )
        try:
            recovered = await self.execution.recover_attempt(
                attempt.attempt_id,
                prepared=prepared,
            )
        except Exception as exc:
            reason = str(exc)
            self.attempt_store.update(attempt.attempt_id, status=AttemptStatus.FAILED)
            self._record_task_state(
                attempt.task_id,
                TaskState.BLOCKED,
                active_attempt_id=attempt.attempt_id,
                failure_reason=reason,
            )
            self._set_snapshot(
                stage=TaskLoopStage.BLOCKED,
                active_lease=lease,
                active_attempt_id=attempt.attempt_id,
                blocking_reason=reason,
            )
            return _AttemptRecoveryResult(
                task_result=TaskResult(
                    task_id=attempt.task_id,
                    outcome="failed",
                    error=reason,
                )
            )
        self._record_task_state(
            recovered.task_id,
            TaskState.ACTIVE,
            active_attempt_id=recovered.attempt_id,
        )
        return _AttemptRecoveryResult(attempt=recovered)

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
        return self.review_ticket_store.get(ticket_id)

    def list_pending_review_tickets(self) -> list[ReviewTicket]:
        return self.review_ticket_store.list_pending()

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
        patch = retry_definition_patch(prompt_patch=prompt_patch, acceptance_patch=acceptance_patch)
        if patch:
            ticket = self._require_ticket(ticket_id)
            self.roadmap_store.update_task_definition(ticket.task_id, patch)
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

    def _resolve_review_ticket(
        self,
        ticket_id: str,
        command: ReviewResolutionCommand,
    ) -> ReviewResolutionRecord:
        ticket = self._require_ticket(ticket_id)
        merge_outcome: MergeOutcome | None = None
        follow_up_ticket_id: str | None = None
        next_stage = TaskLoopStage.IDLE
        next_active_lease = self._snapshot.active_lease
        next_active_attempt_id: str | None = None
        next_blocking_reason: str | None = None

        if command.decision == "accept":
            attempt = self.attempt_store.get(ticket.attempt_id)
            if attempt is None:
                raise KeyError(f"Attempt not found for review ticket: {ticket.attempt_id}")
            workspace = self.workspace_service.get_workspace(task_id=ticket.task_id, workspace_id=attempt.workspace_id)
            self.attempt_store.update(ticket.attempt_id, status=AttemptStatus.MERGE_PENDING)
            self._set_snapshot(
                stage=TaskLoopStage.MERGE_PENDING,
                active_lease=self._snapshot.active_lease,
                active_attempt_id=ticket.attempt_id,
                blocking_reason=None,
            )
            merge_outcome = self.workspace_service.merge_task_result(workspace)
            if merge_outcome.status == "merged":
                self.attempt_store.update(ticket.attempt_id, status=AttemptStatus.ACCEPTED)
                self._record_task_state(
                    ticket.task_id,
                    TaskState.ACCEPTED,
                    active_attempt_id=None,
                )
                self._maybe_complete_workflow()
                next_stage = (
                    TaskLoopStage.COMPLETED
                    if self.workflow_state_store.load().workflow_status is WorkflowStatus.COMPLETED
                    else TaskLoopStage.IDLE
                )
                next_active_lease = None
            else:
                self.attempt_store.update(ticket.attempt_id, status=AttemptStatus.REVIEW_PENDING)
                follow_up = self.review_ticket_store.create(
                    task_id=ticket.task_id,
                    attempt_id=ticket.attempt_id,
                    run_id=ticket.run_id,
                    review_kind="merge_failure",
                    conversation_id=ticket.conversation_id,
                    summary=merge_outcome.message,
                    diff_ref=ticket.diff_ref,
                )
                follow_up_ticket_id = follow_up.ticket_id
                next_stage = TaskLoopStage.REVIEW_PENDING
                next_active_attempt_id = ticket.attempt_id
        elif command.decision == "retry":
            self.attempt_store.update(ticket.attempt_id, status=AttemptStatus.RETRY_PENDING)
            self._requeue_task_for_retry(ticket.task_id)
            next_active_lease = None
        else:
            self.attempt_store.update(ticket.attempt_id, status=AttemptStatus.ESCALATED)
            self._record_task_state(
                ticket.task_id,
                TaskState.ESCALATED,
                active_attempt_id=None,
            )
            next_stage = TaskLoopStage.BLOCKED
            next_active_lease = None
            next_blocking_reason = command.failure_reason or "Task escalated"

        resolution = ReviewResolutionRecord(
            ticket_id=ticket.ticket_id,
            task_id=ticket.task_id,
            attempt_id=ticket.attempt_id,
            decision=command.decision,
            applied=True,
            merge_outcome=merge_outcome,
            follow_up_ticket_id=follow_up_ticket_id,
        )
        self.review_ticket_store.resolve(
            ticket_id,
            resolution,
            status=self._review_ticket_status_for_resolution(command),
            reason=command.failure_reason,
        )
        self._set_snapshot(
            stage=next_stage,
            active_lease=next_active_lease,
            active_attempt_id=next_active_attempt_id,
            blocking_reason=next_blocking_reason,
        )
        return resolution

    def _require_ticket(self, ticket_id: str) -> ReviewTicket:
        ticket = self.review_ticket_store.get(ticket_id)
        if ticket is None:
            raise KeyError(f"Review ticket not found: {ticket_id}")
        return ticket

    def _create_review_ticket(self, completion, diff_ref: str | None) -> ReviewTicket:
        return self.review_ticket_store.create(
            task_id=completion.task_id,
            attempt_id=completion.attempt_id,
            run_id=completion.code_run_id,
            review_kind="task_result",
            conversation_id=completion.conversation_ref,
            summary=completion.summary,
            diff_ref=diff_ref,
        )

    def _maybe_complete_workflow(self) -> None:
        document = self.roadmap_store.load()
        if self._workflow_is_complete(document.tasks):
            apply_workflow_status(
                workflow_state_store=self.workflow_state_store,
                agent_run_store=self.agent_run_store,
                consensus_store=self.consensus_store,
                question_store=self.question_store,
                attempt_store=self.attempt_store,
                status=WorkflowStatus.COMPLETED,
            )
            self._set_snapshot(
                stage=TaskLoopStage.COMPLETED,
                active_lease=None,
                active_attempt_id=None,
                blocking_reason=None,
            )
            return
        if self._snapshot.stage is TaskLoopStage.BLOCKED:
            return
        self._set_snapshot(
            stage=TaskLoopStage.IDLE,
            active_lease=None,
            active_attempt_id=None,
            blocking_reason=None,
        )

    def _set_blocked_if_needed(self, reason: str | None) -> None:
        stage = TaskLoopStage.BLOCKED if reason else TaskLoopStage.IDLE
        self._set_snapshot(
            stage=stage,
            active_lease=None,
            active_attempt_id=None,
            blocking_reason=reason,
        )

    def _record_task_state(
        self,
        task_id: str,
        state: TaskState,
        *,
        active_attempt_id: str | None = None,
        failure_reason: str | None = None,
    ) -> TaskInfo:
        task = self._require_task(task_id)
        projected = self._project_task_state(task, state=state, failure_reason=failure_reason)
        return self.roadmap_store.replace_task(
            projected,
            active_attempt_id=active_attempt_id,
        )

    def _require_task(self, task_id: str) -> TaskInfo:
        task = self.roadmap_store.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        return task

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
            pending_review_ticket_ids=self._pending_review_ticket_ids(),
            blocking_reason=blocking_reason,
        )

    def _pending_review_ticket_ids(self) -> tuple[str, ...]:
        return tuple(ticket.ticket_id for ticket in self.review_ticket_store.list_pending())

    @staticmethod
    def _task_execution_block_reason(workflow) -> str | None:
        if workflow.pending_question_ids:
            return "Pending user input blocks task execution."
        if workflow.gatekeeper.lifecycle_state.value == "awaiting_user":
            return "Gatekeeper is awaiting input."
        if workflow.gatekeeper.lifecycle_state.value == "failed":
            return workflow.gatekeeper.last_error or "Gatekeeper is in a failed state."
        return None

    @staticmethod
    def _execution_slots_available(workflow) -> int:
        return workflow.concurrency_limit - len(workflow.active_attempt_ids)

    @staticmethod
    def _accepted_task_ids(tasks: list[TaskInfo]) -> set[str]:
        return {task.id for task in tasks if task.status is TaskStatus.ACCEPTED}

    def _can_dispatch_task(
        self,
        task: TaskInfo,
        *,
        leased_task_ids: set[str],
        has_active_attempt: bool,
        accepted_task_ids: set[str],
    ) -> bool:
        if task.id in leased_task_ids or has_active_attempt:
            return False
        task_state = self._task_state_from_task(task)
        if task_state not in {TaskState.PENDING, TaskState.READY}:
            return False
        return not any(dependency not in accepted_task_ids for dependency in task.dependencies)

    def _task_needs_ready_projection(self, task: TaskInfo) -> bool:
        return self._task_state_from_task(task) is TaskState.PENDING

    @staticmethod
    def _build_dispatch_lease(task: TaskInfo, *, definition_version: int) -> DispatchLease:
        return DispatchLease(
            task_id=task.id,
            lease_id=f"lease-{uuid4()}",
            task_definition_version=definition_version,
            branch_hint=task.branch,
        )

    def _build_attempt_lease(self, attempt) -> DispatchLease:
        task = self._require_task(attempt.task_id)
        return self._build_dispatch_lease(
            task,
            definition_version=attempt.task_definition_version,
        )

    @staticmethod
    def _review_ticket_status_for_resolution(command: ReviewResolutionCommand):
        return {
            "accept": ReviewTicketStatus.ACCEPTED,
            "retry": ReviewTicketStatus.RETRY,
            "escalate": ReviewTicketStatus.ESCALATED,
        }[command.decision]

    @staticmethod
    def _workflow_is_complete(tasks: list[TaskInfo]) -> bool:
        return bool(tasks) and all(task.status is TaskStatus.ACCEPTED for task in tasks)

    @staticmethod
    def _task_state_from_task(task: TaskInfo) -> TaskState:
        return {
            TaskStatus.PENDING: TaskState.PENDING,
            TaskStatus.QUEUED: TaskState.READY,
            TaskStatus.IN_PROGRESS: TaskState.ACTIVE,
            TaskStatus.COMPLETED: TaskState.REVIEW_PENDING,
            TaskStatus.ACCEPTED: TaskState.ACCEPTED,
            TaskStatus.FAILED: TaskState.BLOCKED,
            TaskStatus.ESCALATED: TaskState.ESCALATED,
        }[task.status]

    def _project_task_state(
        self,
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

    def _requeue_task_for_retry(self, task_id: str) -> TaskInfo:
        task = self._require_task(task_id)
        if task.retry_count >= task.max_retries:
            raise ValueError(f"Task has exhausted retries: {task_id}")
        updated = task.model_copy(deep=True)
        updated.retry_count += 1
        updated.status = TaskStatus.QUEUED
        updated.failure_reason = None
        return self.roadmap_store.replace_task(updated, active_attempt_id=None)
