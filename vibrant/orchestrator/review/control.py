"""Review ticket control for the redesigned orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..types import AttemptCompletion, DiffArtifact, MergeOutcome, ReviewResolutionCommand, ReviewResolutionRecord, ReviewTicket, TaskState


@dataclass(slots=True)
class ReviewControlService:
    """Manage attempt-scoped review tickets and their resolution."""

    review_ticket_store: Any
    workflow_policy: Any
    roadmap_store: Any
    workspace_service: Any
    attempt_store: Any

    def create_ticket(self, completion: AttemptCompletion, diff: DiffArtifact | None) -> ReviewTicket:
        ticket = self.review_ticket_store.create(
            task_id=completion.task_id,
            attempt_id=completion.attempt_id,
            agent_id=completion.code_agent_id,
            review_kind="task_result",
            conversation_id=completion.conversation_ref,
            summary=completion.summary,
            diff_ref=diff.path if diff is not None else completion.diff_ref,
        )
        self.workflow_policy.on_review_ticket_created(ticket)
        self.roadmap_store.record_task_state(completion.task_id, TaskState.REVIEW_PENDING)
        return ticket

    def get_ticket(self, ticket_id: str) -> ReviewTicket | None:
        return self.review_ticket_store.get(ticket_id)

    def list_pending(self) -> list[ReviewTicket]:
        return self.review_ticket_store.list_pending()

    def resolve(self, ticket_id: str, command: ReviewResolutionCommand) -> ReviewResolutionRecord:
        ticket = self.review_ticket_store.get(ticket_id)
        if ticket is None:
            raise KeyError(f"Review ticket not found: {ticket_id}")

        merge_outcome: MergeOutcome | None = None
        follow_up_ticket_id: str | None = None

        if command.decision == "accept":
            attempt = self.attempt_store.get(ticket.attempt_id)
            if attempt is None:
                raise KeyError(f"Attempt not found for review ticket: {ticket.attempt_id}")
            workspace = self.workspace_service.get_workspace(task_id=ticket.task_id, workspace_id=attempt.workspace_id)
            merge_outcome = self.workspace_service.merge_task_result(workspace)
            if merge_outcome.status == "merged":
                self.workflow_policy.mark_task_accepted(task_id=ticket.task_id, attempt_id=ticket.attempt_id)
                self.roadmap_store.record_task_state(ticket.task_id, TaskState.ACCEPTED)
            else:
                follow_up = self.review_ticket_store.create(
                    task_id=ticket.task_id,
                    attempt_id=ticket.attempt_id,
                    agent_id=ticket.agent_id,
                    review_kind="merge_failure",
                    conversation_id=ticket.conversation_id,
                    summary=merge_outcome.message,
                    diff_ref=ticket.diff_ref,
                )
                follow_up_ticket_id = follow_up.ticket_id
        elif command.decision == "retry":
            self.workflow_policy.requeue_task(task_id=ticket.task_id, attempt_id=ticket.attempt_id)
            self.roadmap_store.record_task_state(ticket.task_id, TaskState.READY)
        else:
            self.workflow_policy.mark_task_escalated(task_id=ticket.task_id, attempt_id=ticket.attempt_id)
            self.roadmap_store.record_task_state(ticket.task_id, TaskState.ESCALATED)

        resolution = ReviewResolutionRecord(
            ticket_id=ticket.ticket_id,
            task_id=ticket.task_id,
            attempt_id=ticket.attempt_id,
            decision=command.decision,
            applied=True,
            merge_outcome=merge_outcome,
            follow_up_ticket_id=follow_up_ticket_id,
        )
        self.review_ticket_store.resolve(ticket_id, resolution, reason=command.failure_reason)
        return resolution
