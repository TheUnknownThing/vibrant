"""Stable facade over the layered orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vibrant.config import RoadmapExecutionMode
from vibrant.consensus.roadmap import RoadmapDocument
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus
from vibrant.models.state import OrchestratorStatus
from vibrant.models.task import TaskInfo

from .policy.gatekeeper_loop.questions import current_pending_question, select_pending_question_by_text
from .policy.gatekeeper_loop.transitions import (
    can_transition_ui_status,
    infer_resume_status,
    plan_ui_transition,
)
from .policy.shared.workflow import orchestrator_status_from_workflow
from .types import (
    AgentInstanceSnapshot,
    AgentRunSnapshot,
    QuestionPriority,
    QuestionView,
    RoleSnapshot,
    WorkflowStatus,
)

@dataclass(frozen=True)
class OrchestratorSnapshot:
    status: OrchestratorStatus
    pending_questions: tuple[str, ...]
    question_records: tuple[QuestionView, ...]
    roadmap: RoadmapDocument | None
    consensus: ConsensusDocument | None
    consensus_path: Path | None
    roles: tuple[RoleSnapshot, ...]
    instances: tuple[AgentInstanceSnapshot, ...]
    runs: tuple[AgentRunSnapshot, ...]
    execution_mode: RoadmapExecutionMode | None
    user_input_banner: str
    notification_bell_enabled: bool


@dataclass(slots=True)
class _RoleReadView:
    facade: "OrchestratorFacade"

    def list(self) -> list[RoleSnapshot]:
        return self.facade.list_roles()

    def get(self, role: str) -> RoleSnapshot | None:
        return self.facade.get_role(role)


@dataclass(slots=True)
class _InstanceReadView:
    facade: "OrchestratorFacade"

    def list(self, *, role: str | None = None, active_only: bool = False) -> list[AgentInstanceSnapshot]:
        return self.facade.list_instances(role=role, active_only=active_only)

    def active(self, *, role: str | None = None) -> list[AgentInstanceSnapshot]:
        return self.facade.list_instances(role=role, active_only=True)

    def get(self, agent_id: str) -> AgentInstanceSnapshot | None:
        return self.facade.get_instance(agent_id)


@dataclass(slots=True)
class _RunReadView:
    facade: "OrchestratorFacade"

    def list(
        self,
        *,
        task_id: str | None = None,
        role: str | None = None,
        agent_id: str | None = None,
        include_completed: bool = True,
        active_only: bool = False,
    ) -> list[AgentRunSnapshot]:
        return self.facade.list_runs(
            task_id=task_id,
            role=role,
            agent_id=agent_id,
            include_completed=include_completed,
            active_only=active_only,
        )

    def active(
        self,
        *,
        task_id: str | None = None,
        role: str | None = None,
        agent_id: str | None = None,
    ) -> list[AgentRunSnapshot]:
        return self.facade.list_runs(
            task_id=task_id,
            role=role,
            agent_id=agent_id,
            include_completed=True,
            active_only=True,
        )

    def get(self, run_id: str) -> AgentRunSnapshot | None:
        return self.facade.get_run(run_id)

    def for_task(
        self,
        task_id: str,
        *,
        role: str | None = None,
        include_completed: bool = True,
    ) -> list[AgentRunSnapshot]:
        return self.facade.list_runs(
            task_id=task_id,
            role=role,
            include_completed=include_completed,
        )

    def for_instance(
        self,
        agent_id: str,
        *,
        include_completed: bool = True,
    ) -> list[AgentRunSnapshot]:
        return self.facade.list_runs(agent_id=agent_id, include_completed=include_completed)

    def latest_for_task(self, task_id: str, *, role: str | None = None) -> AgentRunSnapshot | None:
        runs = self.for_task(task_id, role=role)
        return runs[-1] if runs else None


class OrchestratorFacade:
    """UI-facing facade backed by the layered interface adapter."""

    def __init__(self, orchestrator) -> None:
        self.orchestrator = orchestrator
        self.control_plane = orchestrator.control_plane
        self.roles = _RoleReadView(self)
        self.instances = _InstanceReadView(self)
        self.runs = _RunReadView(self)

    def snapshot(self) -> OrchestratorSnapshot:
        pending = self.list_pending_question_records()
        return OrchestratorSnapshot(
            status=self.get_workflow_status(),
            pending_questions=tuple(question.text for question in pending),
            question_records=tuple(self.list_question_records()),
            roadmap=self.control_plane.get_roadmap(),
            consensus=self.control_plane.get_consensus_document(),
            consensus_path=self.orchestrator.consensus_path,
            roles=tuple(self.list_roles()),
            instances=tuple(self.list_instances()),
            runs=tuple(self.list_runs()),
            execution_mode=self.orchestrator.execution_mode,
            user_input_banner=self.get_user_input_banner(),
            notification_bell_enabled=False,
        )

    def get_workflow_status(self) -> OrchestratorStatus:
        return orchestrator_status_from_workflow(self.control_plane.get_workflow_status())

    def get_workflow_session(self):
        return self.control_plane.workflow_session()

    def get_gatekeeper_session(self):
        return self.control_plane.gatekeeper_session()

    def get_consensus_document(self) -> ConsensusDocument | None:
        return self.control_plane.get_consensus_document()

    def get_roadmap(self) -> RoadmapDocument:
        return self.control_plane.get_roadmap()

    def get_consensus_source_path(self) -> Path | None:
        return self.orchestrator.consensus_path

    def list_roles(self) -> list[RoleSnapshot]:
        return self.control_plane.list_roles()

    def get_role(self, role: str) -> RoleSnapshot | None:
        return self.control_plane.get_role(role)

    def list_instances(
        self,
        *,
        role: str | None = None,
        active_only: bool = False,
    ) -> list[AgentInstanceSnapshot]:
        instances = self.control_plane.list_instances()
        normalized_role = role.strip().lower() if isinstance(role, str) and role.strip() else None
        return [
            instance
            for instance in instances
            if (normalized_role is None or instance.identity.role == normalized_role)
            and (not active_only or instance.active_run_id is not None)
        ]

    def get_instance(self, agent_id: str) -> AgentInstanceSnapshot | None:
        return self.control_plane.get_instance(agent_id)

    def list_runs(
        self,
        *,
        task_id: str | None = None,
        role: str | None = None,
        agent_id: str | None = None,
        include_completed: bool = True,
        active_only: bool = False,
    ) -> list[AgentRunSnapshot]:
        runs = self.control_plane.list_active_runs() if active_only else self.control_plane.list_runs()
        normalized_role = role.strip().lower() if isinstance(role, str) and role.strip() else None
        task_run_ids = (
            self.orchestrator.attempt_store.run_ids_for_task(task_id)
            if isinstance(task_id, str) and task_id.strip()
            else None
        )
        snapshots: list[AgentRunSnapshot] = []
        for run in runs:
            if task_run_ids is not None and run.identity.run_id not in task_run_ids:
                continue
            if normalized_role is not None and run.identity.role != normalized_role:
                continue
            if agent_id is not None and run.identity.agent_id != agent_id:
                continue
            if not include_completed and run.runtime.done:
                continue
            snapshots.append(run)
        return snapshots

    def list_active_runs(self) -> list[AgentRunSnapshot]:
        return self.control_plane.list_active_runs()

    def get_run(self, run_id: str) -> AgentRunSnapshot | None:
        return self.control_plane.get_run(run_id)

    def get_attempt_execution(self, attempt_id: str):
        return self.control_plane.get_attempt_execution(attempt_id)

    def get_attempt_execution_session(self, attempt_id: str):
        return self.control_plane.get_attempt_execution_session(attempt_id)

    def get_conversation(self, conversation_id: str):
        return self.control_plane.conversation_session(conversation_id)

    def task_id_for_run(self, run_id: str) -> str | None:
        normalized_run_id = run_id.strip() if isinstance(run_id, str) else ""
        if not normalized_run_id:
            return None
        return self.orchestrator.attempt_store.task_id_for_run(normalized_run_id)

    def list_question_records(self) -> list[QuestionView]:
        return self.control_plane.list_question_records()

    def list_pending_question_records(self) -> list[QuestionView]:
        return self.control_plane.list_pending_question_records()

    def get_task(self, task_id: str) -> TaskInfo | None:
        return self.control_plane.get_task(task_id)

    def add_task(self, task: TaskInfo, *, index: int | None = None) -> TaskInfo:
        return self.control_plane.add_task(task, index=index)

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
            task = self.get_task(task_id)
            if task is None:
                raise KeyError(f"Task not found: {task_id}")
            return task
        return self.control_plane.update_task_definition(task_id, **patch)

    def reorder_tasks(self, ordered_task_ids: list[str]) -> RoadmapDocument:
        return self.control_plane.reorder_tasks(ordered_task_ids)

    def replace_roadmap(self, *, tasks: list[TaskInfo], project: str | None = None) -> RoadmapDocument:
        return self.control_plane.replace_roadmap(tasks=tasks, project=project)

    def update_consensus(self, *, status: ConsensusStatus | str | None = None, context: str | None = None) -> ConsensusDocument:
        return self.control_plane.update_consensus(status=status, context=context)

    def append_decision(self, **kwargs: Any) -> ConsensusDocument:
        return self.control_plane.append_decision(**kwargs)

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
    ) -> QuestionView:
        return self.control_plane.request_user_decision(
            text,
            source_agent_id=source_agent_id,
            source_role=source_role,
            priority=priority,
            blocking_scope=blocking_scope,
            task_id=task_id,
            source_conversation_id=source_conversation_id,
            source_turn_id=source_turn_id,
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
    ) -> QuestionView:
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

    def withdraw_question(self, question_id: str, *, reason: str | None = None) -> QuestionView:
        return self.control_plane.withdraw_question(question_id, reason=reason)

    def get_task_summaries(self) -> dict[str, str]:
        summaries: dict[str, tuple[float, str]] = {}
        task_id_by_run_id = self.get_run_task_ids()
        for run in self.list_runs():
            summary = run.outcome.summary
            if not summary:
                continue
            timestamp = (
                run.runtime.finished_at.timestamp()
                if run.runtime.finished_at is not None
                else run.runtime.started_at.timestamp()
                if run.runtime.started_at is not None
                else 0.0
            )
            task_id = task_id_by_run_id.get(run.identity.run_id)
            if task_id is None:
                continue
            previous = summaries.get(task_id)
            if previous is None or timestamp >= previous[0]:
                summaries[task_id] = (timestamp, summary)
        return {task_id: summary for task_id, (_, summary) in summaries.items()}

    def get_run_task_ids(self) -> dict[str, str]:
        return self.orchestrator.attempt_store.run_task_ids()

    def get_user_input_banner(self) -> str:
        question = current_pending_question(self.list_pending_question_records())
        if question is None:
            return "Gatekeeper is idle."
        return f"Gatekeeper needs your input: {question.text}"

    def write_consensus_document(self, document: ConsensusDocument) -> ConsensusDocument:
        return self.control_plane.write_consensus_document(document)

    async def submit_gatekeeper_input(self, text: str, *, question_id: str | None = None):
        submission = await self.control_plane.submit_user_input(text, question_id=question_id)
        return submission, await self.control_plane.wait_for_gatekeeper_submission(submission)

    async def submit_gatekeeper_message(self, text: str):
        _, result = await self.submit_gatekeeper_input(text)
        return result

    async def answer_pending_question(self, answer: str, *, question: str | None = None):
        pending = self.list_pending_question_records()
        selected = select_pending_question_by_text(pending, question)
        if selected is None:
            raise ValueError("No pending Gatekeeper question exists")
        _, result = await self.submit_gatekeeper_input(answer, question_id=selected.question_id)
        return result

    def pause_workflow(self):
        self.control_plane.pause_workflow()
        return self.get_workflow_status()

    def resume_workflow(self):
        self.control_plane.resume_workflow()
        return self.get_workflow_status()

    def end_planning_phase(self) -> OrchestratorStatus:
        self.control_plane.end_planning_phase()
        return self.get_workflow_status()

    def accept_review_ticket(self, ticket_id: str):
        return self.control_plane.accept_review_ticket(ticket_id)

    def retry_review_ticket(
        self,
        ticket_id: str,
        *,
        failure_reason: str,
        prompt_patch: str | None = None,
        acceptance_patch: list[str] | None = None,
    ):
        return self.control_plane.retry_review_ticket(
            ticket_id,
            failure_reason=failure_reason,
            prompt_patch=prompt_patch,
            acceptance_patch=acceptance_patch,
        )

    def escalate_review_ticket(self, ticket_id: str, *, reason: str):
        return self.control_plane.escalate_review_ticket(ticket_id, reason=reason)

    def list_pending_questions(self) -> list[str]:
        return [record.text for record in self.list_pending_question_records()]

    def get_current_pending_question(self) -> str | None:
        question = current_pending_question(self.list_pending_question_records())
        return question.text if question is not None else None

    def can_transition_to(self, next_status: OrchestratorStatus) -> bool:
        current = self.get_workflow_status()
        return can_transition_ui_status(current, next_status)

    def transition_workflow_state(self, next_status: OrchestratorStatus) -> None:
        current_status = self.get_workflow_status()
        plan = plan_ui_transition(current_status, next_status)
        if plan.action == "noop":
            return
        if plan.action == "pause":
            self.control_plane.pause_workflow()
            return
        if plan.action == "resume":
            self.control_plane.resume_workflow()
            return
        if plan.action == "end_planning":
            self.control_plane.end_planning_phase()
            return
        self.control_plane.set_workflow_status(plan.workflow_status)

    def infer_resume_status(self) -> OrchestratorStatus:
        workflow_state = self.orchestrator.workflow_state_store.load()
        if (
            workflow_state.workflow_status is WorkflowStatus.PAUSED
            and workflow_state.resume_status is not None
        ):
            return orchestrator_status_from_workflow(workflow_state.resume_status)
        return infer_resume_status(
            self.get_consensus_document(),
            self.snapshot().roadmap,
        )

    def list_active_attempts(self):
        return self.control_plane.list_active_attempts()

    def get_review_ticket(self, ticket_id: str):
        return self.control_plane.get_review_ticket(ticket_id)

    def list_pending_review_tickets(self):
        return self.control_plane.list_pending_review_tickets()

    def list_recent_events(self, *, limit: int = 20):
        return self.control_plane.list_recent_events(limit=limit)
