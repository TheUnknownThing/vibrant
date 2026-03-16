"""Stable facade over the layered orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vibrant.config import RoadmapExecutionMode
from vibrant.consensus.roadmap import RoadmapDocument
from vibrant.models.consensus import ConsensusDocument
from vibrant.models.task import TaskInfo

from .policy.gatekeeper_loop.questions import current_pending_question, select_pending_question_by_text
from .policy.gatekeeper_loop.transitions import (
    can_transition_ui_status,
    infer_resume_status,
    plan_ui_transition,
)
from .types import (
    AgentInstanceSnapshot,
    AgentRunSnapshot,
    GatekeeperLifecycleStatus,
    QuestionPriority,
    QuestionView,
    RoleSnapshot,
    WorkflowStatus,
)

@dataclass(frozen=True)
class OrchestratorSnapshot:
    status: WorkflowStatus
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
        self._orchestrator = orchestrator
        self._control_plane = orchestrator._control_plane
        self.roles = _RoleReadView(self)
        self.instances = _InstanceReadView(self)
        self.runs = _RunReadView(self)

    def snapshot(self) -> OrchestratorSnapshot:
        pending = self.list_pending_question_records()
        return OrchestratorSnapshot(
            status=self.get_workflow_status(),
            pending_questions=tuple(question.text for question in pending),
            question_records=tuple(self.list_question_records()),
            roadmap=self._control_plane.get_roadmap(),
            consensus=self._control_plane.get_consensus_document(),
            consensus_path=self._orchestrator._consensus_store.path,
            roles=tuple(self.list_roles()),
            instances=tuple(self.list_instances()),
            runs=tuple(self.list_runs()),
            execution_mode=self.get_execution_mode(),
            user_input_banner=self.get_user_input_banner(),
        )

    def get_workflow_status(self) -> WorkflowStatus:
        return self._control_plane.get_workflow_status()

    def workflow_snapshot(self):
        return self._control_plane.workflow_snapshot()

    def workflow_session(self):
        return self._control_plane.workflow_session()

    def gatekeeper_state(self):
        return self._control_plane.gatekeeper_state()

    def gatekeeper_session(self):
        return self._control_plane.gatekeeper_session()

    def task_loop_state(self):
        return self._control_plane.task_loop_state()

    def get_consensus_document(self) -> ConsensusDocument | None:
        return self._control_plane.get_consensus_document()

    def get_roadmap(self) -> RoadmapDocument:
        return self._control_plane.get_roadmap()

    def get_consensus_source_path(self) -> Path | None:
        return self._orchestrator._consensus_store.path

    def get_execution_mode(self) -> RoadmapExecutionMode:
        mode = self._orchestrator._config.execution_mode
        if isinstance(mode, RoadmapExecutionMode):
            return mode
        return RoadmapExecutionMode(str(mode).strip().lower())

    def list_roles(self) -> list[RoleSnapshot]:
        return self._control_plane.list_roles()

    def get_role(self, role: str) -> RoleSnapshot | None:
        return self._control_plane.get_role(role)

    def list_instances(
        self,
        *,
        role: str | None = None,
        active_only: bool = False,
    ) -> list[AgentInstanceSnapshot]:
        instances = self._control_plane.list_instances()
        normalized_role = role.strip().lower() if isinstance(role, str) and role.strip() else None
        return [
            instance
            for instance in instances
            if (normalized_role is None or instance.identity.role == normalized_role)
            and (not active_only or instance.active_run_id is not None)
        ]

    def get_instance(self, agent_id: str) -> AgentInstanceSnapshot | None:
        return self._control_plane.get_instance(agent_id)

    def list_runs(
        self,
        *,
        task_id: str | None = None,
        role: str | None = None,
        agent_id: str | None = None,
        include_completed: bool = True,
        active_only: bool = False,
    ) -> list[AgentRunSnapshot]:
        runs = self._control_plane.list_active_runs() if active_only else self._control_plane.list_runs()
        normalized_role = role.strip().lower() if isinstance(role, str) and role.strip() else None
        task_run_ids = None
        if isinstance(task_id, str) and task_id.strip():
            task_run_ids = {
                run_id
                for run_id, mapped_task_id in self.get_run_task_ids().items()
                if mapped_task_id == task_id
            }
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
        return self._control_plane.list_active_runs()

    def get_run(self, run_id: str) -> AgentRunSnapshot | None:
        return self._control_plane.get_run(run_id)

    def get_attempt_execution(self, attempt_id: str):
        return self._control_plane.get_attempt_execution(attempt_id)

    def get_conversation(self, conversation_id: str):
        return self._control_plane.conversation_session(conversation_id)

    def conversation(self, conversation_id: str):
        return self._control_plane.conversation(conversation_id)

    def subscribe_conversation(self, conversation_id: str, callback, *, replay: bool = False):
        return self._control_plane.subscribe_conversation(conversation_id, callback, replay=replay)

    def gatekeeper_conversation_id(self) -> str | None:
        return self._control_plane.gatekeeper_conversation_id()

    def subscribe_runtime_events(
        self,
        callback,
        *,
        agent_id: str | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        event_types=None,
    ):
        return self._control_plane.subscribe_runtime_events(
            callback,
            agent_id=agent_id,
            run_id=run_id,
            task_id=task_id,
            event_types=event_types,
        )

    def task_id_for_run(self, run_id: str) -> str | None:
        normalized_run_id = run_id.strip() if isinstance(run_id, str) else ""
        if not normalized_run_id:
            return None
        return self._control_plane.task_id_for_run(normalized_run_id)

    def list_question_records(self) -> list[QuestionView]:
        return self._control_plane.list_question_records()

    def get_question(self, question_id: str) -> QuestionView | None:
        return self._control_plane.get_question(question_id)

    def list_pending_question_records(self) -> list[QuestionView]:
        return self._control_plane.list_pending_question_records()

    def get_task(self, task_id: str) -> TaskInfo | None:
        return self._control_plane.get_task(task_id)

    def add_task(self, task: TaskInfo, *, index: int | None = None) -> TaskInfo:
        return self._control_plane.add_task(task, index=index)

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
        return self._control_plane.update_task_definition(task_id, **patch)

    def reorder_tasks(self, ordered_task_ids: list[str]) -> RoadmapDocument:
        return self._control_plane.reorder_tasks(ordered_task_ids)

    def replace_roadmap(self, *, tasks: list[TaskInfo], project: str | None = None) -> RoadmapDocument:
        return self._control_plane.replace_roadmap(tasks=tasks, project=project)

    def update_consensus(self, *, context: str | None = None) -> ConsensusDocument:
        return self._control_plane.update_consensus(context=context)

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
        return self._control_plane.request_user_decision(
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
        return self._control_plane.withdraw_question(question_id, reason=reason)

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
        return self._control_plane.run_task_ids()

    def get_user_input_banner(self) -> str:
        question = current_pending_question(self.list_pending_question_records())
        if question is None:
            return "Gatekeeper is idle."
        return f"Gatekeeper needs your input: {question.text}"

    def write_consensus_document(self, document: ConsensusDocument) -> ConsensusDocument:
        return self._control_plane.write_consensus_document(document)

    async def submit_user_message(self, text: str):
        return await self._control_plane.submit_user_input(text)

    async def answer_user_decision(self, question_id: str, answer: str):
        return await self._control_plane.submit_user_input(answer, question_id=question_id)

    async def wait_for_gatekeeper_submission(self, submission):
        return await self._control_plane.wait_for_gatekeeper_submission(submission)

    async def respond_to_gatekeeper_request(
        self,
        run_id: str,
        request_id: int | str,
        *,
        result: object | None = None,
        error: dict[str, object] | None = None,
    ):
        return await self._control_plane.respond_to_gatekeeper_request(
            run_id,
            request_id,
            result=result,
            error=error,
        )

    async def submit_gatekeeper_input(self, text: str, *, question_id: str | None = None):
        submission = await self._control_plane.submit_user_input(text, question_id=question_id)
        return submission, await self._control_plane.wait_for_gatekeeper_submission(submission)

    async def submit_gatekeeper_message(self, text: str):
        _, result = await self.submit_gatekeeper_input(text)
        return result

    async def run_next_task(self):
        return await self._control_plane.run_next_task()

    async def run_until_blocked(self):
        return await self._control_plane.run_until_blocked()

    async def interrupt_gatekeeper(self) -> bool:
        if not self._control_plane.gatekeeper_busy():
            return False
        session = await self._control_plane.interrupt_gatekeeper()
        return session.lifecycle_state in {
            GatekeeperLifecycleStatus.RUNNING,
            GatekeeperLifecycleStatus.AWAITING_USER,
            GatekeeperLifecycleStatus.IDLE,
        }

    async def answer_pending_question(self, answer: str, *, question: str | None = None):
        pending = self.list_pending_question_records()
        selected = select_pending_question_by_text(pending, question)
        if selected is None:
            raise ValueError("No pending Gatekeeper question exists")
        _, result = await self.submit_gatekeeper_input(answer, question_id=selected.question_id)
        return result

    def pause_workflow(self):
        self._control_plane.pause_workflow()
        return self.get_workflow_status()

    def resume_workflow(self):
        self._control_plane.resume_workflow()
        return self.get_workflow_status()

    def end_planning_phase(self) -> WorkflowStatus:
        self._control_plane.end_planning_phase()
        return self.get_workflow_status()

    def accept_review_ticket(self, ticket_id: str):
        return self._control_plane.accept_review_ticket(ticket_id)

    def retry_review_ticket(
        self,
        ticket_id: str,
        *,
        failure_reason: str,
        prompt_patch: str | None = None,
        acceptance_patch: list[str] | None = None,
    ):
        return self._control_plane.retry_review_ticket(
            ticket_id,
            failure_reason=failure_reason,
            prompt_patch=prompt_patch,
            acceptance_patch=acceptance_patch,
        )

    def escalate_review_ticket(self, ticket_id: str, *, reason: str):
        return self._control_plane.escalate_review_ticket(ticket_id, reason=reason)

    def list_pending_questions(self) -> list[str]:
        return [record.text for record in self.list_pending_question_records()]

    def get_current_pending_question(self) -> str | None:
        question = current_pending_question(self.list_pending_question_records())
        return question.text if question is not None else None

    def can_transition_to(self, next_status: WorkflowStatus) -> bool:
        current = self.get_workflow_status()
        return can_transition_ui_status(current, next_status)

    def transition_workflow_state(self, next_status: WorkflowStatus) -> None:
        current_status = self.get_workflow_status()
        plan = plan_ui_transition(current_status, next_status)
        if plan.action == "noop":
            return
        if plan.action == "pause":
            self._control_plane.pause_workflow()
            return
        if plan.action == "resume":
            self._control_plane.resume_workflow()
            return
        if plan.action == "begin_planning":
            self._control_plane.begin_planning_phase()
            return
        if plan.action == "end_planning":
            self._control_plane.end_planning_phase()
            return
        raise ValueError(f"Unhandled workflow transition action: {plan.action}")

    def infer_resume_status(self) -> WorkflowStatus:
        workflow_session = self.workflow_session()
        if workflow_session.status is WorkflowStatus.PAUSED and workflow_session.resume_status is not None:
            return workflow_session.resume_status
        return infer_resume_status(
            self.snapshot().roadmap,
        )

    def list_active_attempts(self):
        return self._control_plane.list_active_attempts()

    def list_attempt_executions(self, *, task_id: str | None = None, status=None):
        return self._control_plane.list_attempt_executions(task_id=task_id, status=status)

    def get_review_ticket(self, ticket_id: str):
        return self._control_plane.get_review_ticket(ticket_id)

    def list_review_tickets(self, *, task_id: str | None = None, status=None):
        return self._control_plane.list_review_tickets(task_id=task_id, status=status)

    def list_pending_review_tickets(self):
        return self._control_plane.list_pending_review_tickets()

    def list_recent_events(self, *, limit: int = 20):
        return self._control_plane.list_recent_events(limit=limit)

    def gatekeeper_busy(self) -> bool:
        return self._control_plane.gatekeeper_busy()
