"""Public orchestrator facade used by the UI and MCP surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from vibrant.agents.gatekeeper import GatekeeperRunResult
from vibrant.config import RoadmapExecutionMode
from vibrant.consensus import RoadmapDocument
from vibrant.models.agent import AgentRecord, AgentType
from vibrant.models.consensus import ConsensusDocument
from vibrant.models.consensus import ConsensusStatus
from vibrant.models.state import OrchestratorStatus, QuestionPriority, QuestionRecord
from vibrant.models.task import TaskInfo, TaskStatus

from .agents.manager import ManagedAgentSnapshot
from .bootstrap import Orchestrator
from .task_dispatch import TaskDispatcher
from .types import OrchestratorAgentSnapshot, TaskResult

_WORKFLOW_TO_CONSENSUS = {
    OrchestratorStatus.INIT: ConsensusStatus.INIT,
    OrchestratorStatus.PLANNING: ConsensusStatus.PLANNING,
    OrchestratorStatus.EXECUTING: ConsensusStatus.EXECUTING,
    OrchestratorStatus.PAUSED: ConsensusStatus.PAUSED,
    OrchestratorStatus.COMPLETED: ConsensusStatus.COMPLETED,
}


@dataclass(frozen=True)
class OrchestratorSnapshot:
    """Stable read model for orchestrator-backed consumers."""

    status: OrchestratorStatus
    pending_questions: tuple[str, ...]
    question_records: tuple[QuestionRecord, ...]
    roadmap: RoadmapDocument | None
    consensus: ConsensusDocument | None
    consensus_path: Path
    agent_records: tuple[AgentRecord, ...]
    execution_mode: RoadmapExecutionMode
    user_input_banner: str
    notification_bell_enabled: bool


class OrchestratorFacade:
    """Single entry point for orchestrator-backed app operations."""

    def __init__(self, orchestrator: Orchestrator) -> None:
        self.orchestrator = orchestrator
        self.questions = orchestrator.question_service

    @staticmethod
    def _snapshot_from_managed(snapshot: ManagedAgentSnapshot) -> OrchestratorAgentSnapshot:
        return OrchestratorAgentSnapshot(
            agent_id=snapshot.agent_id,
            task_id=snapshot.task_id,
            agent_type=snapshot.agent_type,
            status=snapshot.status,
            state=snapshot.state,
            has_handle=snapshot.has_handle,
            active=snapshot.active,
            done=snapshot.done,
            awaiting_input=snapshot.awaiting_input,
            pid=snapshot.pid,
            branch=snapshot.branch,
            worktree_path=snapshot.worktree_path,
            started_at=snapshot.started_at,
            finished_at=snapshot.finished_at,
            summary=snapshot.summary,
            error=snapshot.error,
            provider_thread_id=snapshot.provider_thread_id,
            provider_thread_path=snapshot.provider_thread_path,
            provider_resume_cursor=snapshot.provider_resume_cursor,
            input_requests=list(snapshot.input_requests),
            native_event_log=snapshot.native_event_log,
            canonical_event_log=snapshot.canonical_event_log,
            output=snapshot.output,
        )

    @staticmethod
    def _task_summary_timestamp(record: AgentRecord) -> float:
        if record.started_at is not None:
            return float(record.started_at.timestamp())
        if record.finished_at is not None:
            return float(record.finished_at.timestamp())
        return 0.0

    @property
    def roadmap_document(self) -> RoadmapDocument | None:
        return self.orchestrator.roadmap_document

    @property
    def execution_mode(self) -> RoadmapExecutionMode:
        return self.orchestrator.execution_mode

    def snapshot(self) -> OrchestratorSnapshot:
        state_store = self.orchestrator.state_store
        return OrchestratorSnapshot(
            status=state_store.status,
            pending_questions=tuple(self.questions.pending_questions()),
            question_records=tuple(self.questions.records()),
            roadmap=self.orchestrator.roadmap_document,
            consensus=state_store.consensus,
            consensus_path=self.orchestrator.consensus_path,
            agent_records=tuple(self.orchestrator.agent_manager.list_records()),
            execution_mode=self.orchestrator.execution_mode,
            user_input_banner=state_store.user_input_banner(),
            notification_bell_enabled=state_store.notification_bell_enabled(),
        )

    def workflow_status(self) -> OrchestratorStatus:
        return self.orchestrator.state_store.status

    def consensus_document(self) -> ConsensusDocument | None:
        return self.orchestrator.state_store.consensus

    def roadmap(self) -> RoadmapDocument | None:
        return self.orchestrator.roadmap_document

    def consensus_source_path(self) -> Path:
        return self.orchestrator.consensus_path

    def agent_records(self) -> list[AgentRecord]:
        return self.orchestrator.agent_manager.list_records()

    def get_agent(self, agent_id: str) -> OrchestratorAgentSnapshot | None:
        snapshot = self.orchestrator.agent_manager.get_agent(agent_id)
        if snapshot is None:
            return None
        return self._snapshot_from_managed(snapshot)

    def list_agents(
        self,
        *,
        task_id: str | None = None,
        agent_type: AgentType | str | None = None,
        include_completed: bool = True,
        active_only: bool = False,
    ) -> list[OrchestratorAgentSnapshot]:
        return [
            self._snapshot_from_managed(snapshot)
            for snapshot in self.orchestrator.agent_manager.list_agents(
                task_id=task_id,
                agent_type=agent_type,
                include_completed=include_completed,
                active_only=active_only,
            )
        ]

    def list_active_agents(self) -> list[OrchestratorAgentSnapshot]:
        return [
            self._snapshot_from_managed(snapshot)
            for snapshot in self.orchestrator.agent_manager.list_active_agents()
        ]

    def question_records(self) -> list[QuestionRecord]:
        return self.questions.records()

    def pending_question_records(self) -> list[QuestionRecord]:
        return self.questions.pending_records()

    def task(self, task_id: str) -> TaskInfo | None:
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
        document.tasks = tasks
        roadmap_service.dispatcher = TaskDispatcher(
            tasks,
            concurrency_limit=self.orchestrator.state_store.state.concurrency_limit,
        )
        roadmap_service.persist()
        return document

    def update_consensus(
        self,
        *,
        status: ConsensusStatus | str | None = None,
        objectives: str | None = None,
        getting_started: str | None = None,
        questions: Sequence[str] | None = None,
    ) -> ConsensusDocument:
        return self.orchestrator.consensus_service.update(
            status=status,
            objectives=objectives,
            getting_started=getting_started,
            questions=questions,
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
        questions: list[str] | tuple[str, ...],
        *,
        source_agent_id: str | None = None,
        source_role: str = "gatekeeper",
    ) -> list[QuestionRecord]:
        return list(
            self.questions.sync_pending(
                questions,
                source_agent_id=source_agent_id,
                source_role=source_role,
            )
        )

    def resolve_question(self, question_id: str, *, answer: str | None = None) -> QuestionRecord:
        return self.questions.resolve(question_id, answer=answer)

    def task_summaries(self) -> dict[str, str]:
        by_task: dict[str, tuple[float, str]] = {}
        for record in self.orchestrator.agent_manager.list_records():
            if not record.summary:
                continue
            sort_key = self._task_summary_timestamp(record)
            previous = by_task.get(record.task_id)
            if previous is None or sort_key >= previous[0]:
                by_task[record.task_id] = (sort_key, record.summary)
        return {task_id: summary for task_id, (_, summary) in by_task.items()}

    def user_input_banner(self) -> str:
        return self.orchestrator.state_store.user_input_banner()

    def notification_bell_enabled(self) -> bool:
        return self.orchestrator.state_store.notification_bell_enabled()

    def write_consensus_document(self, document: ConsensusDocument) -> ConsensusDocument:
        written = self.orchestrator.consensus_service.write(document)
        self.orchestrator.state_store.refresh()
        return written

    async def submit_gatekeeper_message(self, text: str) -> GatekeeperRunResult:
        return await self.orchestrator.submit_gatekeeper_message(text)

    async def answer_pending_question(self, answer: str, *, question: str | None = None) -> GatekeeperRunResult:
        return await self.questions.answer(answer, question=question)

    def pause_workflow(self) -> None:
        if self.workflow_status() is OrchestratorStatus.PAUSED:
            return
        self.transition_workflow_state(OrchestratorStatus.PAUSED)

    def resume_workflow(self) -> None:
        current = self.workflow_status()
        if current is OrchestratorStatus.EXECUTING:
            return
        if current is not OrchestratorStatus.PAUSED:
            raise ValueError(f"Cannot resume workflow from {current.value}")
        self.transition_workflow_state(OrchestratorStatus.EXECUTING)

    def end_planning_phase(self) -> OrchestratorStatus:
        self.orchestrator.workflow_service.begin_execution_if_needed()
        self.orchestrator.state_store.refresh()
        return self.workflow_status()

    def review_task_outcome(self, task_id: str, *, decision: str, failure_reason: str | None = None) -> TaskInfo:
        normalized_decision = decision.strip().lower()
        task = self.task(task_id)
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
        acceptance_criteria: list[str] | tuple[str, ...] | None = None,
    ) -> TaskInfo:
        task = self.task(task_id)
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

    def pending_questions(self) -> list[str]:
        return self.questions.pending_questions()

    def current_pending_question(self) -> str | None:
        return self.questions.current_question()

    def reload_from_disk(self) -> RoadmapDocument:
        return self.orchestrator.reload_from_disk()

    async def execute_until_blocked(self) -> list[TaskResult]:
        return await self.orchestrator.execute_until_blocked()

    async def execute_next_task(self) -> TaskResult | None:
        return await self.orchestrator.execute_next_task()

    def can_transition_to(self, next_status: OrchestratorStatus) -> bool:
        return self.orchestrator.state_store.can_transition_to(next_status)

    def transition_workflow_state(self, next_status: OrchestratorStatus) -> None:
        current = self.orchestrator.state_store.status
        if current is next_status:
            return
        if not self.can_transition_to(next_status):
            raise ValueError(f"Invalid orchestrator state transition: {current.value} -> {next_status.value}")

        self._sync_consensus_status(next_status)
        self.orchestrator.state_store.transition_to(next_status)
        self.orchestrator.state_store.refresh()

    def _sync_consensus_status(self, next_status: OrchestratorStatus) -> None:
        target_consensus_status = _WORKFLOW_TO_CONSENSUS.get(next_status)
        if target_consensus_status is not None:
            self.orchestrator.consensus_service.set_status(target_consensus_status)


__all__ = [
    "OrchestratorFacade",
    "OrchestratorSnapshot",
]
