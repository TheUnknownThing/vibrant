"""Stable facade over the redesigned orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vibrant.config import RoadmapExecutionMode
from vibrant.consensus.roadmap import RoadmapDocument
from vibrant.models.agent import AgentRecord
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus
from vibrant.models.state import OrchestratorStatus
from vibrant.models.task import TaskInfo, TaskStatus

from .types import (
    AgentOutput,
    AgentSnapshotIdentity,
    AgentSnapshotOutcome,
    AgentSnapshotProvider,
    AgentSnapshotRuntime,
    AgentSnapshotWorkspace,
    OrchestratorAgentSnapshot,
    QuestionPriority,
    QuestionRecord,
    ReviewResolutionCommand,
    TaskState,
    WorkflowStatus,
)


_WORKFLOW_TO_UI = {
    WorkflowStatus.INIT: OrchestratorStatus.INIT,
    WorkflowStatus.PLANNING: OrchestratorStatus.PLANNING,
    WorkflowStatus.EXECUTING: OrchestratorStatus.EXECUTING,
    WorkflowStatus.PAUSED: OrchestratorStatus.PAUSED,
    WorkflowStatus.COMPLETED: OrchestratorStatus.COMPLETED,
    WorkflowStatus.FAILED: OrchestratorStatus.PAUSED,
}

_UI_TO_WORKFLOW = {
    OrchestratorStatus.INIT: WorkflowStatus.INIT,
    OrchestratorStatus.PLANNING: WorkflowStatus.PLANNING,
    OrchestratorStatus.EXECUTING: WorkflowStatus.EXECUTING,
    OrchestratorStatus.PAUSED: WorkflowStatus.PAUSED,
    OrchestratorStatus.COMPLETED: WorkflowStatus.COMPLETED,
}

_WORKFLOW_TO_CONSENSUS = {
    WorkflowStatus.INIT: ConsensusStatus.INIT,
    WorkflowStatus.PLANNING: ConsensusStatus.PLANNING,
    WorkflowStatus.EXECUTING: ConsensusStatus.EXECUTING,
    WorkflowStatus.PAUSED: ConsensusStatus.PAUSED,
    WorkflowStatus.COMPLETED: ConsensusStatus.COMPLETED,
    WorkflowStatus.FAILED: ConsensusStatus.FAILED,
}

_TASK_STATUS_TO_STATE = {
    TaskStatus.PENDING: TaskState.PENDING,
    TaskStatus.QUEUED: TaskState.READY,
    TaskStatus.IN_PROGRESS: TaskState.ACTIVE,
    TaskStatus.COMPLETED: TaskState.REVIEW_PENDING,
    TaskStatus.ACCEPTED: TaskState.ACCEPTED,
    TaskStatus.FAILED: TaskState.BLOCKED,
    TaskStatus.ESCALATED: TaskState.ESCALATED,
}


@dataclass(frozen=True)
class OrchestratorSnapshot:
    status: OrchestratorStatus
    pending_questions: tuple[str, ...]
    question_records: tuple[QuestionRecord, ...]
    roadmap: RoadmapDocument | None
    consensus: ConsensusDocument | None
    consensus_path: Path | None
    agent_records: tuple[AgentRecord, ...]
    execution_mode: RoadmapExecutionMode | None
    user_input_banner: str
    notification_bell_enabled: bool


class OrchestratorFacade:
    """Compatibility-safe facade backed by the redesigned control plane."""

    def __init__(self, orchestrator) -> None:
        self.orchestrator = orchestrator

    def snapshot(self) -> OrchestratorSnapshot:
        pending = self.question_store.list_pending()
        return OrchestratorSnapshot(
            status=self.get_workflow_status(),
            pending_questions=tuple(question.text for question in pending),
            question_records=tuple(self.question_store.list()),
            roadmap=self.orchestrator.roadmap_store.load(),
            consensus=self.orchestrator.consensus_store.load(),
            consensus_path=self.orchestrator.consensus_path,
            agent_records=tuple(self.orchestrator.agent_record_store.list()),
            execution_mode=self.orchestrator.execution_mode,
            user_input_banner=self.get_user_input_banner(),
            notification_bell_enabled=False,
        )

    @property
    def workflow_state_store(self):
        return self.orchestrator.workflow_state_store

    @property
    def question_store(self):
        return self.orchestrator.question_store

    @property
    def consensus_store(self):
        return self.orchestrator.consensus_store

    @property
    def roadmap_store(self):
        return self.orchestrator.roadmap_store

    @property
    def review_control(self):
        return self.orchestrator.review_control

    @property
    def review_ticket_store(self):
        return self.orchestrator.review_ticket_store

    @property
    def attempt_store(self):
        return self.orchestrator.attempt_store

    @property
    def agent_record_store(self):
        return self.orchestrator.agent_record_store

    def get_workflow_status(self) -> OrchestratorStatus:
        return _WORKFLOW_TO_UI[self.workflow_state_store.load().workflow_status]

    def get_consensus_document(self) -> ConsensusDocument | None:
        return self.consensus_store.load()

    def get_roadmap(self) -> RoadmapDocument:
        return self.roadmap_store.load()

    def get_consensus_source_path(self) -> Path | None:
        return self.orchestrator.consensus_path

    def list_agent_records(self) -> list[AgentRecord]:
        return self.agent_record_store.list()

    def get_agent(self, agent_id: str) -> OrchestratorAgentSnapshot | None:
        record = self.agent_record_store.get(agent_id)
        if record is None:
            return None
        return self._snapshot_agent(record)

    def list_agents(
        self,
        *,
        task_id: str | None = None,
        agent_type: str | None = None,
        include_completed: bool = True,
        active_only: bool = False,
    ) -> list[OrchestratorAgentSnapshot]:
        records = self.agent_record_store.list_active() if active_only else self.agent_record_store.list()
        snapshots: list[OrchestratorAgentSnapshot] = []
        for record in records:
            if task_id is not None and record.identity.task_id != task_id:
                continue
            if agent_type is not None and record.identity.type.value != str(agent_type):
                continue
            if not include_completed and record.lifecycle.status.value in {"completed", "failed", "killed"}:
                continue
            snapshots.append(self._snapshot_agent(record))
        return snapshots

    def list_active_agents(self) -> list[OrchestratorAgentSnapshot]:
        return [self._snapshot_agent(record) for record in self.agent_record_store.list_active()]

    def agent_output(self, agent_id: str) -> AgentOutput | None:
        return None

    def list_question_records(self) -> list[QuestionRecord]:
        return self.question_store.list()

    def list_pending_question_records(self) -> list[QuestionRecord]:
        return self.question_store.list_pending()

    def get_task(self, task_id: str) -> TaskInfo | None:
        return self.roadmap_store.get_task(task_id)

    def add_task(self, task: TaskInfo, *, index: int | None = None) -> TaskInfo:
        self.roadmap_store.add_task(task, index=index)
        return self.roadmap_store.get_task(task.id)

    def update_task_definition(
        self,
        task_id: str,
        *,
        title: str | None = None,
        acceptance_criteria: list[str] | None = None,
        branch: str | None = None,
        prompt: str | None = None,
        skills: list[str] | None = None,
        dependencies: list[str] | None = None,
        priority: int | None = None,
        max_retries: int | None = None,
    ) -> TaskInfo:
        patch = {
            key: value
            for key, value in {
                "title": title,
                "acceptance_criteria": acceptance_criteria,
                "branch": branch,
                "prompt": prompt,
                "skills": skills,
                "dependencies": dependencies,
                "priority": priority,
                "max_retries": max_retries,
            }.items()
            if value is not None
        }
        if not patch:
            task = self.roadmap_store.get_task(task_id)
            if task is None:
                raise KeyError(f"Task not found: {task_id}")
            return task
        return self.roadmap_store.update_task_definition(task_id, patch)

    def update_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        acceptance_criteria: list[str] | None = None,
        status: TaskStatus | str | None = None,
        branch: str | None = None,
        retry_count: int | None = None,
        max_retries: int | None = None,
        prompt: str | None = None,
        skills: list[str] | None = None,
        dependencies: list[str] | None = None,
        priority: int | None = None,
        failure_reason: str | None = None,
    ) -> TaskInfo:
        task = self.update_task_definition(
            task_id,
            title=title,
            acceptance_criteria=acceptance_criteria,
            branch=branch,
            prompt=prompt,
            skills=skills,
            dependencies=dependencies,
            priority=priority,
            max_retries=max_retries,
        )
        if status is not None:
            parsed = status if isinstance(status, TaskStatus) else TaskStatus(str(status))
            task = self.roadmap_store.record_task_state(
                task_id,
                _TASK_STATUS_TO_STATE[parsed],
                failure_reason=failure_reason,
            )
        if retry_count is not None and task.retry_count != retry_count:
            task.retry_count = retry_count
            document = self.roadmap_store.load()
            self.roadmap_store.write(document)
        return self.roadmap_store.get_task(task_id)

    def reorder_tasks(self, ordered_task_ids: list[str]) -> RoadmapDocument:
        return self.roadmap_store.reorder_tasks(ordered_task_ids)

    def replace_roadmap(self, *, tasks: list[TaskInfo], project: str | None = None) -> RoadmapDocument:
        document = RoadmapDocument(project=project or self.roadmap_store.load().project, tasks=list(tasks))
        return self.roadmap_store.write(document)

    def update_consensus(self, *, status: ConsensusStatus | str | None = None, context: str | None = None) -> ConsensusDocument:
        document = self.consensus_store.load() or ConsensusDocument(project=self.orchestrator.project_root.name)
        if context is not None:
            document.context = context
        if status is not None:
            document.status = status if isinstance(status, ConsensusStatus) else ConsensusStatus(str(status).upper())
        return self.consensus_store.write(document)

    def append_decision(self, **kwargs: Any) -> ConsensusDocument:
        return self.consensus_store.append_decision(**kwargs)

    def ask_question(
        self,
        text: str,
        *,
        source_agent_id: str | None = None,
        source_role: str = "gatekeeper",
        priority: QuestionPriority = QuestionPriority.BLOCKING,
        blocking_scope: str = "planning",
        task_id: str | None = None,
        source_conversation_id: str | None = None,
        source_turn_id: str | None = None,
    ) -> QuestionRecord:
        return self.question_store.create(
            text=text,
            priority=priority,
            source_role=source_role,
            source_agent_id=source_agent_id,
            source_conversation_id=source_conversation_id,
            source_turn_id=source_turn_id,
            blocking_scope=blocking_scope,
            task_id=task_id,
        )

    def request_user_decision(
        self,
        text: str,
        *,
        source_agent_id: str | None = None,
        source_role: str = "gatekeeper",
        priority: QuestionPriority = QuestionPriority.BLOCKING,
        blocking_scope: str = "planning",
        task_id: str | None = None,
        source_conversation_id: str | None = None,
        source_turn_id: str | None = None,
    ) -> QuestionRecord:
        return self.ask_question(
            text,
            source_agent_id=source_agent_id,
            source_role=source_role,
            priority=priority,
            blocking_scope=blocking_scope,
            task_id=task_id,
            source_conversation_id=source_conversation_id,
            source_turn_id=source_turn_id,
        )

    def withdraw_question(self, question_id: str, *, reason: str | None = None) -> QuestionRecord:
        return self.question_store.withdraw(question_id, reason=reason)

    def set_pending_questions(
        self,
        questions: list[str],
        *,
        source_agent_id: str | None = None,
        source_role: str = "gatekeeper",
    ) -> list[QuestionRecord]:
        pending = self.question_store.list_pending()
        pending_by_text = {record.text: record for record in pending}
        desired = {question.strip() for question in questions if question.strip()}

        next_records: list[QuestionRecord] = []
        for text in desired:
            existing = pending_by_text.get(text)
            if existing is not None:
                next_records.append(existing)
            else:
                next_records.append(
                    self.ask_question(
                        text,
                        source_agent_id=source_agent_id,
                        source_role=source_role,
                    )
                )

        for record in pending:
            if record.text not in desired:
                self.question_store.withdraw(record.question_id, reason="Superseded by compatibility sync")

        return next_records

    def resolve_question(self, question_id: str, *, answer: str | None = None) -> QuestionRecord:
        return self.question_store.resolve(question_id, answer=answer)

    def get_task_summaries(self) -> dict[str, str]:
        summaries: dict[str, tuple[float, str]] = {}
        for record in self.agent_record_store.list():
            summary = record.outcome.summary
            if not summary:
                continue
            timestamp = (
                record.lifecycle.finished_at.timestamp()
                if record.lifecycle.finished_at is not None
                else record.lifecycle.started_at.timestamp()
                if record.lifecycle.started_at is not None
                else 0.0
            )
            previous = summaries.get(record.identity.task_id)
            if previous is None or timestamp >= previous[0]:
                summaries[record.identity.task_id] = (timestamp, summary)
        return {task_id: summary for task_id, (_, summary) in summaries.items()}

    def get_user_input_banner(self) -> str:
        pending = self.question_store.list_pending()
        if not pending:
            return "Gatekeeper is idle."
        question = pending[0].text
        return f"Gatekeeper needs your input: {question}"

    def write_consensus_document(self, document: ConsensusDocument) -> ConsensusDocument:
        return self.consensus_store.write(document)

    async def submit_gatekeeper_message(self, text: str):
        submission = await self.orchestrator.control_plane.submit_user_message(text)
        if not submission.agent_id:
            raise RuntimeError("Gatekeeper submission did not produce an agent id")
        return await self.orchestrator.runtime_service.wait_for_run(submission.agent_id)

    async def answer_pending_question(self, answer: str, *, question: str | None = None):
        pending = self.question_store.list_pending()
        if not pending:
            raise ValueError("No pending Gatekeeper question exists")
        selected = next((record for record in pending if question and record.text == question), pending[0])
        submission = await self.orchestrator.control_plane.answer_user_decision(selected.question_id, answer)
        if not submission.agent_id:
            raise RuntimeError("Gatekeeper answer submission did not produce an agent id")
        return await self.orchestrator.runtime_service.wait_for_run(submission.agent_id)

    def pause_workflow(self):
        self.transition_workflow_state(OrchestratorStatus.PAUSED)
        return self.get_workflow_status()

    def resume_workflow(self):
        self.transition_workflow_state(OrchestratorStatus.EXECUTING)
        return self.get_workflow_status()

    def end_planning_phase(self) -> OrchestratorStatus:
        self.transition_workflow_state(OrchestratorStatus.EXECUTING)
        return self.get_workflow_status()

    def accept_review_ticket(self, ticket_id: str):
        return self.review_control.resolve(ticket_id, ReviewResolutionCommand(decision="accept"))

    def retry_review_ticket(
        self,
        ticket_id: str,
        *,
        failure_reason: str,
        prompt_patch: str | None = None,
        acceptance_patch: list[str] | None = None,
    ):
        ticket = self.review_control.get_ticket(ticket_id)
        if ticket is None:
            raise KeyError(f"Review ticket not found: {ticket_id}")
        if prompt_patch is not None or acceptance_patch is not None:
            self.update_task_definition(
                ticket.task_id,
                prompt=prompt_patch,
                acceptance_criteria=acceptance_patch,
            )
        return self.review_control.resolve(
            ticket_id,
            ReviewResolutionCommand(
                decision="retry",
                failure_reason=failure_reason,
                prompt_patch=prompt_patch,
                acceptance_patch=acceptance_patch,
            ),
        )

    def escalate_review_ticket(self, ticket_id: str, *, reason: str):
        return self.review_control.resolve(
            ticket_id,
            ReviewResolutionCommand(decision="escalate", failure_reason=reason),
        )

    def review_task_outcome(self, task_id: str, *, decision: str, failure_reason: str | None = None) -> TaskInfo:
        ticket = next((item for item in self.review_control.list_pending() if item.task_id == task_id), None)
        if ticket is None:
            raise KeyError(f"No pending review ticket for task {task_id}")
        normalized = decision.strip().lower()
        if normalized in {"accept", "accepted", "approve", "approved"}:
            self.accept_review_ticket(ticket.ticket_id)
        elif normalized in {"retry", "reject", "needs_changes"}:
            self.retry_review_ticket(ticket.ticket_id, failure_reason=failure_reason or "Gatekeeper requested retry")
        else:
            self.escalate_review_ticket(ticket.ticket_id, reason=failure_reason or "Gatekeeper escalated the task")
        task = self.roadmap_store.get_task(task_id)
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
    ) -> TaskInfo:
        ticket = next((item for item in self.review_control.list_pending() if item.task_id == task_id), None)
        if ticket is not None:
            self.retry_review_ticket(
                ticket.ticket_id,
                failure_reason=failure_reason,
                prompt_patch=prompt,
                acceptance_patch=acceptance_criteria,
            )
        else:
            if prompt is not None or acceptance_criteria is not None:
                self.update_task_definition(task_id, prompt=prompt, acceptance_criteria=acceptance_criteria)
            self.roadmap_store.record_task_state(task_id, TaskState.READY, failure_reason=failure_reason)
        task = self.roadmap_store.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found after retry mark: {task_id}")
        return task

    def list_pending_questions(self) -> list[str]:
        return [record.text for record in self.question_store.list_pending()]

    def get_current_pending_question(self) -> str | None:
        pending = self.question_store.list_pending()
        return pending[0].text if pending else None

    def can_transition_to(self, next_status: OrchestratorStatus) -> bool:
        current = self.get_workflow_status()
        if current is OrchestratorStatus.COMPLETED:
            return next_status is OrchestratorStatus.COMPLETED
        return next_status in _UI_TO_WORKFLOW

    def transition_workflow_state(self, next_status: OrchestratorStatus) -> None:
        workflow_status = _UI_TO_WORKFLOW[next_status]
        self.workflow_state_store.update_workflow_status(workflow_status)
        consensus_status = _WORKFLOW_TO_CONSENSUS[workflow_status]
        document = self.consensus_store.load()
        if document is not None:
            document.status = consensus_status
            self.consensus_store.write(document)

    def list_active_attempts(self):
        return self.attempt_store.list_active()

    def get_review_ticket(self, ticket_id: str):
        return self.review_control.get_ticket(ticket_id)

    def list_pending_review_tickets(self):
        return self.review_control.list_pending()

    def list_recent_events(self, *, limit: int = 20):
        return self.orchestrator.list_recent_events(limit=limit)

    def _snapshot_agent(self, record: AgentRecord) -> OrchestratorAgentSnapshot:
        try:
            runtime_snapshot = self.orchestrator.runtime_service.snapshot_handle(record.identity.agent_id)
            state = runtime_snapshot.state
            awaiting_input = runtime_snapshot.awaiting_input
            input_requests = runtime_snapshot.input_requests
            has_handle = True
        except Exception:
            state = record.lifecycle.status.value
            awaiting_input = False
            input_requests = []
            has_handle = False

        return OrchestratorAgentSnapshot(
            identity=AgentSnapshotIdentity(
                agent_id=record.identity.agent_id,
                task_id=record.identity.task_id,
                agent_type=record.identity.type.value,
            ),
            runtime=AgentSnapshotRuntime(
                status=record.lifecycle.status.value,
                state=state,
                has_handle=has_handle,
                active=record.lifecycle.status.value in {"spawning", "connecting", "running", "awaiting_input"},
                done=record.lifecycle.status.value in {"completed", "failed", "killed"},
                awaiting_input=awaiting_input,
                pid=record.lifecycle.pid,
                started_at=record.lifecycle.started_at,
                finished_at=record.lifecycle.finished_at,
                input_requests=input_requests,
            ),
            workspace=AgentSnapshotWorkspace(
                branch=record.context.branch,
                worktree_path=record.context.worktree_path,
            ),
            outcome=AgentSnapshotOutcome(
                summary=record.outcome.summary,
                error=record.outcome.error,
                output=None,
            ),
            provider=AgentSnapshotProvider(
                thread_id=record.provider.provider_thread_id,
                thread_path=record.provider.thread_path,
                resume_cursor=record.provider.resume_cursor,
                native_event_log=record.provider.native_event_log,
                canonical_event_log=record.provider.canonical_event_log,
            ),
        )
