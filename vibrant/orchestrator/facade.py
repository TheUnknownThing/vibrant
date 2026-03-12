"""Public orchestrator facade used by the UI and MCP surfaces."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from vibrant.agents.gatekeeper import GatekeeperRunResult
from vibrant.config import RoadmapExecutionMode
from vibrant.consensus import RoadmapDocument
from vibrant.models.agent import AgentRecord, AgentType
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus
from vibrant.models.state import OrchestratorStatus, QuestionPriority, QuestionRecord
from vibrant.models.task import TaskInfo, TaskStatus
from vibrant.providers.base import CanonicalEvent

from .bootstrap import Orchestrator
from .types import AgentOutput, OrchestratorAgentSnapshot

_WORKFLOW_TO_CONSENSUS = {
    OrchestratorStatus.INIT: ConsensusStatus.INIT,
    OrchestratorStatus.PLANNING: ConsensusStatus.PLANNING,
    OrchestratorStatus.EXECUTING: ConsensusStatus.EXECUTING,
    OrchestratorStatus.PAUSED: ConsensusStatus.PAUSED,
    OrchestratorStatus.COMPLETED: ConsensusStatus.COMPLETED,
}

RawEventHandler = Callable[[CanonicalEvent], Awaitable[None] | None]
AgentUpdateHandler = Callable[[OrchestratorAgentSnapshot], Awaitable[None] | None]


@dataclass(frozen=True)
class OrchestratorSnapshot:
    """Stable read model for orchestrator-backed consumers."""

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
    """Single entry point for orchestrator-backed app operations."""

    def __init__(self, orchestrator: Orchestrator) -> None:
        self.orchestrator = orchestrator
        self.questions = orchestrator.question_service

    @staticmethod
    def _task_summary_timestamp(record: AgentRecord) -> float:
        if record.lifecycle.started_at is not None:
            return float(record.lifecycle.started_at.timestamp())
        if record.lifecycle.finished_at is not None:
            return float(record.lifecycle.finished_at.timestamp())
        return 0.0

    @property
    def roadmap_document(self) -> RoadmapDocument | None:
        return self.orchestrator.roadmap_document

    @property
    def execution_mode(self) -> RoadmapExecutionMode | None:
        return self.orchestrator.execution_mode

    def snapshot(self) -> OrchestratorSnapshot:
        state_store = self.orchestrator.state_store
        return OrchestratorSnapshot(
            status=state_store.status,
            pending_questions=tuple(self.questions.pending_questions()),
            question_records=tuple(self.questions.records()),
            roadmap=self.orchestrator.roadmap_document,
            consensus=self.orchestrator.consensus_service.current(),
            consensus_path=self.orchestrator.consensus_path,
            agent_records=tuple(self.orchestrator.agent_manager.list_records()),
            execution_mode=self.orchestrator.execution_mode,
            user_input_banner=state_store.user_input_banner(),
            notification_bell_enabled=state_store.notification_bell_enabled(),
        )

    def get_workflow_status(self) -> OrchestratorStatus:
        return self.orchestrator.state_store.status

    def get_consensus_document(self) -> ConsensusDocument | None:
        return self.orchestrator.consensus_service.current()

    def get_roadmap(self) -> RoadmapDocument | None:
        return self.orchestrator.roadmap_document

    def get_consensus_source_path(self) -> Path | None:
        return self.orchestrator.consensus_path

    def list_agent_records(self) -> list[AgentRecord]:
        return self.orchestrator.agent_manager.list_records()

    def get_agent(self, agent_id: str) -> OrchestratorAgentSnapshot | None:
        return self.orchestrator.agent_manager.get_agent(agent_id)

    def list_agents(
        self,
        *,
        task_id: str | None = None,
        agent_type: AgentType | str | None = None,
        include_completed: bool = True,
        active_only: bool = False,
    ) -> list[OrchestratorAgentSnapshot]:
        return self.orchestrator.agent_manager.list_agents(
            task_id=task_id,
            agent_type=agent_type,
            include_completed=include_completed,
            active_only=active_only,
        )

    def list_active_agents(self) -> list[OrchestratorAgentSnapshot]:
        return self.orchestrator.agent_manager.list_active_agents()

    def agent_output(self, agent_id: str) -> AgentOutput | None:
        snapshot = self.get_agent(agent_id)
        if snapshot is not None:
            return snapshot.outcome.output
        return self.orchestrator.agent_output_service.output_for_agent(agent_id)

    def list_question_records(self) -> list[QuestionRecord]:
        return self.questions.records()

    def list_pending_question_records(self) -> list[QuestionRecord]:
        return self.questions.pending_records()

    def get_task(self, task_id: str) -> TaskInfo | None:
        return self.orchestrator.roadmap_service.get_task(task_id)

    def add_task(self, task: TaskInfo, *, index: int | None = None) -> TaskInfo:
        return self.orchestrator.roadmap_service.add_task(task, index=index)

    def update_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        acceptance_criteria: Sequence[str] | None = None,
        status: TaskStatus | str | None = None,
        branch: str | None = None,
        retry_count: int | None = None,
        max_retries: int | None = None,
        prompt: str | None = None,
        skills: Sequence[str] | None = None,
        dependencies: Sequence[str] | None = None,
        priority: int | None = None,
        failure_reason: str | None = None,
    ) -> TaskInfo:
        return self.orchestrator.roadmap_service.update_task(
            task_id,
            title=title,
            acceptance_criteria=acceptance_criteria,
            status=status,
            branch=branch,
            retry_count=retry_count,
            max_retries=max_retries,
            prompt=prompt,
            skills=skills,
            dependencies=dependencies,
            priority=priority,
            failure_reason=failure_reason,
        )

    def reorder_tasks(self, ordered_task_ids: list[str]) -> RoadmapDocument:
        return self.orchestrator.roadmap_service.reorder_tasks(ordered_task_ids)

    def replace_roadmap(self, *, tasks: list[TaskInfo], project: str | None = None) -> RoadmapDocument:
        roadmap_service = self.orchestrator.roadmap_service
        document = roadmap_service._ensure_document()
        roadmap_service.parser.validate_dependency_graph(tasks)

        document.project = project or document.project
        document.tasks = list(tasks)
        roadmap_service._sync_dispatcher(
            concurrency_limit=self.orchestrator.state_store.state.concurrency_limit
        )
        roadmap_service.persist()
        return document

    def update_consensus(
        self,
        *,
        status: ConsensusStatus | str | None = None,
        context: str | None = None,
    ) -> ConsensusDocument:
        return self.orchestrator.consensus_service.update(
            status=status,
            context=context,
        )

    def ask_question(
        self,
        text: str,
        *,
        source_agent_id: str | None = None,
        source_role: str = "gatekeeper",
        priority: QuestionPriority = QuestionPriority.BLOCKING,
    ) -> QuestionRecord:
        return self.questions.ask(
            text,
            source_agent_id=source_agent_id,
            source_role=source_role,
            priority=priority,
        )

    def request_user_decision(
        self,
        text: str,
        *,
        source_agent_id: str | None = None,
        source_role: str = "gatekeeper",
        priority: QuestionPriority = QuestionPriority.BLOCKING,
    ) -> QuestionRecord:
        return self.ask_question(
            text,
            source_agent_id=source_agent_id,
            source_role=source_role,
            priority=priority,
        )

    def set_pending_questions(
        self,
        questions: Sequence[str],
        *,
        source_agent_id: str | None = None,
        source_role: str = "gatekeeper",
    ) -> list[QuestionRecord]:
        return self.questions.sync_pending(
            questions,
            source_agent_id=source_agent_id,
            source_role=source_role,
        )

    def resolve_question(self, question_id: str, *, answer: str | None = None) -> QuestionRecord:
        return self.questions.resolve(question_id, answer=answer)

    def get_task_summaries(self) -> dict[str, str]:
        by_task: dict[str, tuple[float, str]] = {}
        for record in self.orchestrator.agent_manager.list_records():
            summary = record.outcome.summary
            if not summary:
                continue

            task_id = record.identity.task_id
            sort_key = self._task_summary_timestamp(record)
            previous = by_task.get(task_id)
            if previous is None or sort_key >= previous[0]:
                by_task[task_id] = (sort_key, summary)

        return {task_id: summary for task_id, (_, summary) in by_task.items()}

    def get_user_input_banner(self) -> str:
        return self.orchestrator.state_store.user_input_banner()

    def is_notification_bell_enabled(self) -> bool:
        return self.orchestrator.state_store.notification_bell_enabled()

    def write_consensus_document(self, document: ConsensusDocument) -> ConsensusDocument:
        written = self.orchestrator.consensus_service.write(document)
        self.orchestrator.state_store.refresh()
        return written

    async def submit_gatekeeper_message(self, text: str) -> GatekeeperRunResult:
        return await self.orchestrator.submit_gatekeeper_message(text)

    async def answer_pending_question(
        self,
        answer: str,
        *,
        question: str | None = None,
    ) -> GatekeeperRunResult:
        return await self.questions.answer(answer, question=question)

    def pause_workflow(self) -> None:
        if self.get_workflow_status() is OrchestratorStatus.PAUSED:
            return
        self.transition_workflow_state(OrchestratorStatus.PAUSED)

    def resume_workflow(self) -> None:
        current = self.get_workflow_status()
        if current is OrchestratorStatus.EXECUTING:
            return
        if current is not OrchestratorStatus.PAUSED:
            raise ValueError(f"Cannot resume workflow from {current.value}")
        self.transition_workflow_state(OrchestratorStatus.EXECUTING)

    def end_planning_phase(self) -> OrchestratorStatus:
        self.orchestrator.workflow_service.begin_execution_if_needed()
        self.orchestrator.state_store.refresh()
        return self.get_workflow_status()

    def review_task_outcome(self, task_id: str, *, decision: str, failure_reason: str | None = None) -> TaskInfo:
        normalized_decision = decision.strip().lower()
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found in roadmap: {task_id}")

        if normalized_decision in {"accept", "accepted", "approve", "approved", "done"}:
            if task.status is TaskStatus.ACCEPTED:
                return task
            if task.status is not TaskStatus.COMPLETED:
                raise ValueError(f"Cannot accept task from status {task.status.value}")
            return self.update_task(task_id, status=TaskStatus.ACCEPTED)

        if normalized_decision in {"needs_input", "awaiting_input"}:
            return task

        if normalized_decision in {"reject", "rejected", "retry", "needs_changes"}:
            if task.status is TaskStatus.COMPLETED:
                return self.update_task(
                    task_id,
                    status=TaskStatus.FAILED,
                    failure_reason=failure_reason or "Gatekeeper requested changes",
                )
            return task

        if normalized_decision in {"escalate", "escalated"}:
            if task.status is TaskStatus.ESCALATED:
                return task
            if task.status is TaskStatus.COMPLETED:
                task = self.update_task(
                    task_id,
                    status=TaskStatus.FAILED,
                    failure_reason=failure_reason or "Gatekeeper escalated the task",
                )
            if task.status is TaskStatus.FAILED and task.can_transition_to(TaskStatus.ESCALATED):
                return self.update_task(task_id, status=TaskStatus.ESCALATED)
            return task

        raise ValueError(f"Unsupported Gatekeeper review decision: {decision}")

    def mark_task_for_retry(
        self,
        task_id: str,
        *,
        failure_reason: str,
        prompt: str | None = None,
        acceptance_criteria: Sequence[str] | None = None,
    ) -> TaskInfo:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found in roadmap: {task_id}")

        if prompt is not None:
            task = self.update_task(task_id, prompt=prompt)
        if acceptance_criteria is not None:
            task = self.update_task(task_id, acceptance_criteria=acceptance_criteria)

        if task.status is TaskStatus.COMPLETED:
            task = self.update_task(task_id, status=TaskStatus.FAILED, failure_reason=failure_reason)

        if task.status is TaskStatus.FAILED:
            next_status = TaskStatus.QUEUED if task.can_transition_to(TaskStatus.QUEUED) else TaskStatus.ESCALATED
            task = self.update_task(task_id, status=next_status)

        if task.status not in {TaskStatus.QUEUED, TaskStatus.ESCALATED}:
            raise ValueError(f"Cannot mark task for retry from status {task.status.value}")
        return task

    def list_pending_questions(self) -> list[str]:
        return self.questions.pending_questions()

    def get_current_pending_question(self) -> str | None:
        return self.questions.current_question()

    def can_transition_to(self, next_status: OrchestratorStatus) -> bool:
        return self.orchestrator.state_store.can_transition_to(next_status)

    def transition_workflow_state(self, next_status: OrchestratorStatus) -> None:
        current = self.orchestrator.state_store.status
        if current is next_status:
            return
        if not self.can_transition_to(next_status):
            raise ValueError(f"Invalid orchestrator state transition: {current.value} -> {next_status.value}")

        self._sync_consensus_status(next_status)
        if self.orchestrator.state_store.status is next_status:
            return
        self.orchestrator.state_store.transition_to(next_status)
        self.orchestrator.state_store.refresh()

    def _sync_consensus_status(self, next_status: OrchestratorStatus) -> None:
        target_status = _WORKFLOW_TO_CONSENSUS.get(next_status)
        if target_status is not None:
            self.orchestrator.consensus_service.set_status(target_status)


__all__ = [
    "OrchestratorFacade",
    "OrchestratorSnapshot",
]
