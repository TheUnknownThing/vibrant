"""Command adapter over policy loops and basic stores."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vibrant.consensus.roadmap import RoadmapDocument
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus
from vibrant.models.task import TaskInfo

from ..policy.gatekeeper_loop import GatekeeperUserLoop
from ..policy.task_loop import TaskLoop
from ..types import QuestionPriority, QuestionView, ReviewResolutionRecord, WorkflowSnapshot, WorkflowStatus


@dataclass(slots=True)
class PolicyCommandAdapter:
    """Expose explicit command operations for first-party interfaces."""

    gatekeeper_loop: GatekeeperUserLoop
    task_loop: TaskLoop

    async def submit_user_input(self, text: str, question_id: str | None = None):
        return await self.gatekeeper_loop.submit_user_input(text, question_id=question_id)

    async def wait_for_gatekeeper_submission(self, submission):
        return await self.gatekeeper_loop.wait_for_submission(submission)

    async def restart_gatekeeper(self, reason: str | None = None):
        return await self.gatekeeper_loop.restart(reason)

    async def stop_gatekeeper(self):
        return await self.gatekeeper_loop.stop()

    def set_workflow_status(self, status: WorkflowStatus) -> WorkflowSnapshot:
        return self.gatekeeper_loop.transition_workflow(status)

    def end_planning_phase(self) -> WorkflowSnapshot:
        return self.gatekeeper_loop.end_planning()

    def pause_workflow(self) -> WorkflowSnapshot:
        return self.gatekeeper_loop.transition_workflow(WorkflowStatus.PAUSED)

    def resume_workflow(self) -> WorkflowSnapshot:
        return self.gatekeeper_loop.resume_workflow()

    async def run_next_task(self):
        return await self.task_loop.run_next_task()

    async def run_until_blocked(self):
        return await self.task_loop.run_until_blocked()

    def add_task(self, task: TaskInfo, *, index: int | None = None) -> TaskInfo:
        return self.gatekeeper_loop.add_task(task, index=index)

    def update_task_definition(self, task_id: str, **patch: Any) -> TaskInfo:
        return self.gatekeeper_loop.update_task_definition(task_id, **patch)

    def reorder_tasks(self, ordered_task_ids: list[str]) -> RoadmapDocument:
        return self.gatekeeper_loop.reorder_tasks(ordered_task_ids)

    def replace_roadmap(self, *, tasks: list[TaskInfo], project: str | None = None) -> RoadmapDocument:
        return self.gatekeeper_loop.replace_roadmap(tasks=tasks, project=project)

    def update_consensus(
        self,
        *,
        status: ConsensusStatus | str | None = None,
        context: str | None = None,
    ) -> ConsensusDocument:
        return self.gatekeeper_loop.update_consensus(status=status, context=context)

    def write_consensus_document(self, document: ConsensusDocument) -> ConsensusDocument:
        return self.gatekeeper_loop.write_consensus_document(document)

    def request_user_decision(
        self,
        text: str,
        *,
        priority: QuestionPriority = QuestionPriority.BLOCKING,
        blocking_scope: str = "planning",
        task_id: str | None = None,
        source_agent_id: str | None = None,
        source_role: str = "gatekeeper",
        source_conversation_id: str | None = None,
        source_turn_id: str | None = None,
    ) -> QuestionView:
        record = self.gatekeeper_loop.request_user_decision(
            text=text,
            priority=priority,
            source_role=source_role,
            source_agent_id=source_agent_id,
            source_conversation_id=source_conversation_id,
            source_turn_id=source_turn_id,
            blocking_scope=blocking_scope,
            task_id=task_id,
        )
        return QuestionView.from_record(record)

    def withdraw_question(self, question_id: str, *, reason: str | None = None) -> QuestionView:
        record = self.gatekeeper_loop.withdraw_question(question_id, reason=reason)
        return QuestionView.from_record(record)

    def accept_review_ticket(self, ticket_id: str) -> ReviewResolutionRecord:
        return self.task_loop.accept_review_ticket(ticket_id)

    def retry_review_ticket(
        self,
        ticket_id: str,
        *,
        failure_reason: str,
        prompt_patch: str | None = None,
        acceptance_patch: list[str] | None = None,
    ) -> ReviewResolutionRecord:
        return self.task_loop.retry_review_ticket(
            ticket_id,
            failure_reason=failure_reason,
            prompt_patch=prompt_patch,
            acceptance_patch=acceptance_patch,
        )

    def escalate_review_ticket(self, ticket_id: str, *, reason: str) -> ReviewResolutionRecord:
        return self.task_loop.escalate_review_ticket(ticket_id, reason=reason)
