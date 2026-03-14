"""Command adapter over policy loops and artifact mutations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vibrant.consensus.roadmap import RoadmapDocument
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus
from vibrant.models.task import TaskInfo

from ..basic import ArtifactsCapability
from ..policy.gatekeeper_loop import GatekeeperUserLoop
from ..policy.task_loop import TaskLoop
from ..types import QuestionPriority, ReviewResolutionRecord, WorkflowSnapshot, WorkflowStatus


_WORKFLOW_TO_CONSENSUS = {
    WorkflowStatus.INIT: ConsensusStatus.INIT,
    WorkflowStatus.PLANNING: ConsensusStatus.PLANNING,
    WorkflowStatus.EXECUTING: ConsensusStatus.EXECUTING,
    WorkflowStatus.PAUSED: ConsensusStatus.PAUSED,
    WorkflowStatus.COMPLETED: ConsensusStatus.COMPLETED,
    WorkflowStatus.FAILED: ConsensusStatus.FAILED,
}


@dataclass(slots=True)
class PolicyCommandAdapter:
    """Expose explicit command operations for first-party interfaces."""

    project_name: str
    artifacts: ArtifactsCapability
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
        self.artifacts.workflow_state_store.update_workflow_status(status)
        self.artifacts.consensus_store.set_status_projection(_WORKFLOW_TO_CONSENSUS[status])
        return self.artifacts.workflow_snapshot()

    def start_execution(self) -> WorkflowSnapshot:
        return self.set_workflow_status(WorkflowStatus.EXECUTING)

    def pause_workflow(self) -> WorkflowSnapshot:
        return self.set_workflow_status(WorkflowStatus.PAUSED)

    def resume_workflow(self) -> WorkflowSnapshot:
        return self.set_workflow_status(WorkflowStatus.EXECUTING)

    async def run_next_task(self):
        return await self.task_loop.run_next_task()

    async def run_until_blocked(self):
        return await self.task_loop.run_until_blocked()

    def add_task(self, task: TaskInfo, *, index: int | None = None) -> TaskInfo:
        self.artifacts.roadmap_store.add_task(task, index=index)
        created = self.artifacts.roadmap_store.get_task(task.id)
        if created is None:
            raise KeyError(task.id)
        return created

    def update_task_definition(self, task_id: str, **patch: Any) -> TaskInfo:
        return self.artifacts.roadmap_store.update_task_definition(task_id, patch)

    def reorder_tasks(self, ordered_task_ids: list[str]) -> RoadmapDocument:
        return self.artifacts.roadmap_store.reorder_tasks(ordered_task_ids)

    def replace_roadmap(self, *, tasks: list[TaskInfo], project: str | None = None) -> RoadmapDocument:
        return self.artifacts.roadmap_store.replace(tasks=tasks, project=project or self.project_name)

    def update_consensus(
        self,
        *,
        status: ConsensusStatus | str | None = None,
        context: str | None = None,
    ) -> ConsensusDocument:
        document = self.artifacts.consensus_store.load() or ConsensusDocument(project=self.project_name)
        if context is not None:
            document.context = context
        if status is not None:
            document.status = status if isinstance(status, ConsensusStatus) else ConsensusStatus(str(status).upper())
        return self.artifacts.consensus_store.write(document)

    def append_decision(self, **kwargs: Any) -> ConsensusDocument:
        return self.artifacts.consensus_store.append_decision(**kwargs)

    def write_consensus_document(self, document: ConsensusDocument) -> ConsensusDocument:
        return self.artifacts.consensus_store.write(document)

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
    ):
        return self.artifacts.question_store.create(
            text=text,
            priority=priority,
            source_role=source_role,
            source_agent_id=source_agent_id,
            source_conversation_id=source_conversation_id,
            source_turn_id=source_turn_id,
            blocking_scope=blocking_scope,
            task_id=task_id,
        )

    def withdraw_question(self, question_id: str, *, reason: str | None = None):
        return self.artifacts.question_store.withdraw(question_id, reason=reason)

    def set_pending_questions(
        self,
        questions: list[str],
        *,
        source_agent_id: str | None = None,
        source_role: str = "gatekeeper",
    ):
        pending = {record.text: record for record in self.artifacts.question_store.list_pending()}
        desired = {question.strip() for question in questions if question.strip()}

        records = []
        for text in desired:
            existing = pending.get(text)
            if existing is not None:
                records.append(existing)
                continue
            records.append(
                self.request_user_decision(
                    text,
                    source_agent_id=source_agent_id,
                    source_role=source_role,
                )
            )

        for text, record in pending.items():
            if text not in desired:
                self.artifacts.question_store.withdraw(record.question_id, reason="Superseded by compatibility sync")
        return records

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

    def review_task_outcome(self, task_id: str, *, decision: str, failure_reason: str | None = None):
        return self.task_loop.review_task_outcome(task_id, decision=decision, failure_reason=failure_reason)

    def mark_task_for_retry(
        self,
        task_id: str,
        *,
        failure_reason: str,
        prompt: str | None = None,
        acceptance_criteria: list[str] | None = None,
    ):
        return self.task_loop.mark_task_for_retry(
            task_id,
            failure_reason=failure_reason,
            prompt=prompt,
            acceptance_criteria=acceptance_criteria,
        )
