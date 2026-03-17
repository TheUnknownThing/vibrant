"""Review and merge decision helpers for the task loop."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...types import AttemptStatus, MergeOutcome, ReviewResolutionRecord, ReviewTicket, ReviewTicketStatus, WorkflowStatus
from . import task_projection
from .models import ReviewResolutionCommand, TaskLoopStage, TaskState
from .prompting import retry_definition_patch

if TYPE_CHECKING:
    from .loop import TaskLoop


def get_review_ticket(loop: TaskLoop, ticket_id: str) -> ReviewTicket | None:
    return loop.review_ticket_store.get(ticket_id)


def list_pending_review_tickets(loop: TaskLoop) -> list[ReviewTicket]:
    return loop.review_ticket_store.list_pending()


def list_review_tickets(
    loop: TaskLoop,
    *,
    task_id: str | None = None,
    status: ReviewTicketStatus | None = None,
) -> list[ReviewTicket]:
    tickets = (
        loop.review_ticket_store.list_by_task(task_id)
        if task_id is not None
        else loop.review_ticket_store.list_all()
    )
    if status is None:
        return tickets
    return [ticket for ticket in tickets if ticket.status is status]


def accept_review_ticket(loop: TaskLoop, ticket_id: str) -> ReviewResolutionRecord:
    return resolve_review_ticket(loop, ticket_id, ReviewResolutionCommand(decision="accept"))


def retry_review_ticket(
    loop: TaskLoop,
    ticket_id: str,
    *,
    failure_reason: str,
    prompt_patch: str | None = None,
    acceptance_patch: list[str] | None = None,
) -> ReviewResolutionRecord:
    patch = retry_definition_patch(prompt_patch=prompt_patch, acceptance_patch=acceptance_patch)
    if patch:
        ticket = require_ticket(loop, ticket_id)
        loop.roadmap_store.update_task_definition(ticket.task_id, patch)
    return resolve_review_ticket(
        loop,
        ticket_id,
        ReviewResolutionCommand(
            decision="retry",
            failure_reason=failure_reason,
            prompt_patch=prompt_patch,
            acceptance_patch=acceptance_patch,
        ),
    )


def escalate_review_ticket(loop: TaskLoop, ticket_id: str, *, reason: str) -> ReviewResolutionRecord:
    return resolve_review_ticket(
        loop,
        ticket_id,
        ReviewResolutionCommand(decision="escalate", failure_reason=reason),
    )


def resolve_review_ticket(
    loop: TaskLoop,
    ticket_id: str,
    command: ReviewResolutionCommand,
) -> ReviewResolutionRecord:
    ticket = require_ticket(loop, ticket_id)
    merge_outcome: MergeOutcome | None = None
    follow_up_ticket_id: str | None = None
    next_stage = TaskLoopStage.IDLE
    next_active_lease = loop._snapshot.active_lease
    next_active_attempt_id: str | None = None
    next_blocking_reason: str | None = None

    if command.decision == "accept":
        attempt = loop.attempt_store.get(ticket.attempt_id)
        if attempt is None:
            raise KeyError(f"Attempt not found for review ticket: {ticket.attempt_id}")
        workspace = loop.workspace_service.get_workspace(task_id=ticket.task_id, workspace_id=attempt.workspace_id)
        loop.attempt_store.update(ticket.attempt_id, status=AttemptStatus.MERGE_PENDING)
        loop._set_snapshot(
            stage=TaskLoopStage.MERGE_PENDING,
            active_lease=loop._snapshot.active_lease,
            active_attempt_id=ticket.attempt_id,
            blocking_reason=None,
        )
        merge_outcome = loop.workspace_service.merge_task_result(workspace)
        if merge_outcome.status == "merged":
            loop.attempt_store.update(ticket.attempt_id, status=AttemptStatus.ACCEPTED)
            task_projection.record_task_state(
                loop,
                ticket.task_id,
                TaskState.ACCEPTED,
                active_attempt_id=None,
            )
            task_projection.maybe_complete_workflow(loop)
            next_stage = (
                TaskLoopStage.COMPLETED
                if loop.workflow_state_store.load().workflow_status is WorkflowStatus.COMPLETED
                else TaskLoopStage.IDLE
            )
            next_active_lease = None
        else:
            loop.attempt_store.update(ticket.attempt_id, status=AttemptStatus.REVIEW_PENDING)
            follow_up = loop.review_ticket_store.create(
                task_id=ticket.task_id,
                attempt_id=ticket.attempt_id,
                run_id=ticket.run_id,
                review_kind="merge_failure",
                conversation_id=ticket.conversation_id,
                summary=merge_outcome.message,
                diff_ref=ticket.diff_ref,
                base_commit=ticket.base_commit,
                result_commit=ticket.result_commit,
                integration_commit=merge_outcome.integration_commit,
            )
            follow_up_ticket_id = follow_up.ticket_id
            next_stage = TaskLoopStage.REVIEW_PENDING
            next_active_attempt_id = ticket.attempt_id
    elif command.decision == "retry":
        loop.attempt_store.update(ticket.attempt_id, status=AttemptStatus.RETRY_PENDING)
        task_projection.requeue_task_for_retry(loop, ticket.task_id)
        next_active_lease = None
    else:
        loop.attempt_store.update(ticket.attempt_id, status=AttemptStatus.ESCALATED)
        task_projection.record_task_state(
            loop,
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
    loop.review_ticket_store.resolve(
        ticket_id,
        resolution,
        status=task_projection.review_ticket_status_for_resolution(command),
        reason=command.failure_reason,
    )
    loop._set_snapshot(
        stage=next_stage,
        active_lease=next_active_lease,
        active_attempt_id=next_active_attempt_id,
        blocking_reason=next_blocking_reason,
    )
    return resolution


def require_ticket(loop: TaskLoop, ticket_id: str) -> ReviewTicket:
    ticket = loop.review_ticket_store.get(ticket_id)
    if ticket is None:
        raise KeyError(f"Review ticket not found: {ticket_id}")
    return ticket


def create_review_ticket(
    loop: TaskLoop,
    completion,
    *,
    workspace,
    diff_ref: str | None,
) -> ReviewTicket:
    return loop.review_ticket_store.create(
        task_id=completion.task_id,
        attempt_id=completion.attempt_id,
        run_id=completion.code_run_id,
        review_kind="task_result",
        conversation_id=completion.conversation_ref,
        summary=completion.summary,
        diff_ref=diff_ref,
        base_commit=workspace.base_commit,
        result_commit=workspace.result_commit,
        integration_commit=workspace.integration_commit,
    )
