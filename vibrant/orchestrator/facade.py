"""Public orchestrator facade used by the UI and MCP surfaces."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vibrant.agents.gatekeeper import GatekeeperRunResult
from vibrant.agents.role_results import parse_role_result
from vibrant.agents.runtime import NormalizedRunResult
from vibrant.config import RoadmapExecutionMode
from vibrant.consensus import RoadmapDocument
from vibrant.models.agent import AgentRunRecord, ProviderResumeHandle
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus
from vibrant.models.state import OrchestratorStatus, QuestionPriority, QuestionRecord
from vibrant.models.task import TaskInfo, TaskStatus
from vibrant.providers.base import CanonicalEvent

from .bootstrap import Orchestrator
from .types import (
    AgentInstanceSnapshot,
    AgentOutput,
    AgentRoleSnapshot,
    AgentRunContextSnapshot,
    AgentRunOutcomeSnapshot,
    AgentRunRetrySnapshot,
    AgentRunSnapshot,
    AgentSnapshotIdentity,
    DocumentSnapshot,
    ProviderDefaultsSnapshot,
    QuestionAnswerResult,
    RoleSnapshot,
    RunEnvelope,
    RunLifecycleSnapshot,
    RunProviderSnapshot,
    RunRuntimeSnapshot,
    RunWorkspaceSnapshot,
    RuntimeExecutionResult,
    TaskExecutionResult,
    WorkflowSnapshot,
)

_WORKFLOW_TO_CONSENSUS = {
    OrchestratorStatus.INIT: ConsensusStatus.INIT,
    OrchestratorStatus.PLANNING: ConsensusStatus.PLANNING,
    OrchestratorStatus.EXECUTING: ConsensusStatus.EXECUTING,
    OrchestratorStatus.PAUSED: ConsensusStatus.PAUSED,
    OrchestratorStatus.COMPLETED: ConsensusStatus.COMPLETED,
}

RawEventHandler = Callable[[CanonicalEvent], Awaitable[None] | None]
AgentUpdateHandler = Callable[[AgentInstanceSnapshot], Awaitable[None] | None]


@dataclass(frozen=True)
class RoleAPI:
    """Stable role-layer read surface."""

    facade: "OrchestratorFacade"

    @property
    def _manager(self) -> object:
        return self.facade.orchestrator.agent_manager

    def get(self, role: str) -> RoleSnapshot | None:
        return self._manager.get_role(role)

    def list(self) -> list[RoleSnapshot]:
        return self._manager.list_roles()


@dataclass(frozen=True)
class InstanceAPI:
    """Stable agent-instance read/control surface."""

    facade: "OrchestratorFacade"

    @property
    def _manager(self) -> object:
        return self.facade.orchestrator.agent_manager

    @property
    def _output_service(self) -> object:
        return self.facade.orchestrator.agent_output_service

    def get(self, agent_id: str) -> AgentInstanceSnapshot | None:
        return self._manager.get_instance_snapshot(agent_id)

    def list(
        self,
        *,
        task_id: str | None = None,
        role: str | None = None,
        include_completed: bool = True,
        active_only: bool = False,
    ) -> list[AgentInstanceSnapshot]:
        return self._manager.list_instance_snapshots(
            task_id=task_id,
            role=role,
            include_completed=include_completed,
            active_only=active_only,
        )

    def active(self) -> list[AgentInstanceSnapshot]:
        return self._manager.list_active_instance_snapshots()

    def output(self, agent_id: str) -> AgentOutput | None:
        snapshot = self.get(agent_id)
        if snapshot is not None:
            return snapshot.outcome.output
        return self._output_service.output_for_agent(agent_id)

    async def wait(
        self,
        agent_id: str,
        *,
        release_terminal: bool = True,
    ) -> AgentRunSnapshot | object:
        result = await self._manager.wait_for_instance(agent_id, release_terminal=release_terminal)
        if isinstance(result, RuntimeExecutionResult):
            return self.facade._run_snapshot_from_execution_result(result)
        return result

    async def respond_to_request(
        self,
        agent_id: str,
        request_id: int | str,
        *,
        result: object | None = None,
        error: dict[str, object] | None = None,
    ) -> AgentInstanceSnapshot:
        return await self._manager.respond_to_instance_request(
            agent_id,
            request_id,
            result=result,
            error=error,
        )


@dataclass(frozen=True)
class RunAPI:
    """Stable run-history read and observe surface."""

    facade: "OrchestratorFacade"

    @property
    def _manager(self) -> object:
        return self.facade.orchestrator.agent_manager

    def _record(self, run_id: str) -> AgentRunRecord:
        record = self._manager.get_run_record(run_id)
        if record is None:
            raise KeyError(f"Unknown run: {run_id}")
        return record

    def _records(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        role: str | None = None,
    ) -> list[AgentRunRecord]:
        return self._manager.list_run_records(
            task_id=task_id,
            agent_id=agent_id,
            role=role,
        )

    def get(self, run_id: str) -> AgentRunSnapshot | None:
        record = self._manager.get_run_record(run_id)
        if record is None:
            return None
        return self.facade._run_snapshot_from_record(record)

    def list(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        role: str | None = None,
    ) -> list[AgentRunSnapshot]:
        return [
            self.facade._run_snapshot_from_record(record)
            for record in self._records(task_id=task_id, agent_id=agent_id, role=role)
        ]

    def for_task(
        self,
        task_id: str,
        *,
        role: str | None = None,
    ) -> list[AgentRunSnapshot]:
        return self.list(task_id=task_id, role=role)

    def for_instance(self, agent_id: str) -> list[AgentRunSnapshot]:
        return self.list(agent_id=agent_id)

    def events(self, run_id: str) -> list[CanonicalEvent]:
        record = self._record(run_id)
        return _read_canonical_event_log(record.provider.canonical_event_log)

    def subscribe(
        self,
        run_id: str,
        handler: RawEventHandler,
        *,
        event_types: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> Callable[[], None]:
        self._record(run_id)
        return self.facade.orchestrator.subscribe_raw_events(
            handler,
            run_id=run_id,
            event_types=event_types,
        )

    def latest_for_instance(self, agent_id: str) -> AgentRunSnapshot | None:
        records = self._records(agent_id=agent_id)
        if not records:
            return None
        records.sort(key=OrchestratorFacade._task_summary_timestamp)
        return self.facade._run_snapshot_from_record(records[-1])

    def latest_for_task(
        self,
        task_id: str,
        *,
        role: str | None = None,
    ) -> AgentRunSnapshot | None:
        records = self._records(task_id=task_id, role=role)
        if not records:
            return None
        records.sort(key=OrchestratorFacade._task_summary_timestamp)
        return self.facade._run_snapshot_from_record(records[-1])


@dataclass(frozen=True)
class DocumentAPI:
    """Stable document-layer read/write surface."""

    facade: "OrchestratorFacade"

    def roadmap(self) -> RoadmapDocument | None:
        return self.facade.orchestrator.roadmap_document

    def consensus(self) -> ConsensusDocument | None:
        return self.facade.orchestrator.consensus_service.current()

    def consensus_source_path(self) -> Path | None:
        return self.facade.orchestrator.consensus_path

    def snapshot(self) -> DocumentSnapshot:
        return DocumentSnapshot(
            roadmap=self.roadmap(),
            consensus=self.consensus(),
            consensus_path=self.consensus_source_path(),
        )

    def update_consensus(
        self,
        *,
        status: ConsensusStatus | str | None = None,
        context: str | None = None,
    ) -> ConsensusDocument:
        return self.facade.orchestrator.consensus_service.update(
            status=status,
            context=context,
        )

    def write_consensus(self, document: ConsensusDocument) -> ConsensusDocument:
        written = self.facade.orchestrator.consensus_service.write(document)
        self.facade.orchestrator.state_store.refresh()
        return written

    def replace_roadmap(
        self,
        *,
        tasks: list[TaskInfo],
        project: str | None = None,
    ) -> RoadmapDocument:
        roadmap_service = self.facade.orchestrator.roadmap_service
        document = roadmap_service._ensure_document()
        roadmap_service.parser.validate_dependency_graph(tasks)
        document.project = project or document.project
        document.tasks = list(tasks)
        roadmap_service._sync_dispatcher(
            concurrency_limit=self.facade.orchestrator.state_store.state.concurrency_limit
        )
        roadmap_service.persist()
        return document


@dataclass(frozen=True)
class TaskAPI:
    """Stable task-layer read/control surface."""

    facade: "OrchestratorFacade"

    def get(self, task_id: str) -> TaskInfo | None:
        return self.facade.orchestrator.roadmap_service.get_task(task_id)

    def list(self) -> list[TaskInfo]:
        roadmap = self.facade.documents.roadmap()
        if roadmap is None:
            ensure_document = getattr(self.facade.orchestrator.roadmap_service, "_ensure_document", None)
            if callable(ensure_document):
                roadmap = ensure_document()
        return list(roadmap.tasks) if roadmap is not None else []

    def add(self, task: TaskInfo, *, index: int | None = None) -> TaskInfo:
        return self.facade.orchestrator.roadmap_service.add_task(task, index=index)

    def update(
        self,
        task_id: str,
        *,
        title: str | None = None,
        acceptance_criteria: Sequence[str] | None = None,
        status: TaskStatus | str | None = None,
        agent_role: str | None = None,
        branch: str | None = None,
        retry_count: int | None = None,
        max_retries: int | None = None,
        prompt: str | None = None,
        skills: Sequence[str] | None = None,
        dependencies: Sequence[str] | None = None,
        priority: int | None = None,
        failure_reason: str | None = None,
    ) -> TaskInfo:
        return self.facade.orchestrator.roadmap_service.update_task(
            task_id,
            title=title,
            acceptance_criteria=acceptance_criteria,
            status=status,
            agent_role=agent_role,
            branch=branch,
            retry_count=retry_count,
            max_retries=max_retries,
            prompt=prompt,
            skills=skills,
            dependencies=dependencies,
            priority=priority,
            failure_reason=failure_reason,
        )

    def reorder(self, ordered_task_ids: list[str]) -> RoadmapDocument:
        return self.facade.orchestrator.roadmap_service.reorder_tasks(ordered_task_ids)

    def summaries(self) -> dict[str, str]:
        by_task: dict[str, tuple[float, str]] = {}
        for record in self.facade.orchestrator.agent_manager.list_run_records():
            summary = record.outcome.summary
            if not summary:
                continue
            task_id = record.identity.task_id
            sort_key = OrchestratorFacade._task_summary_timestamp(record)
            previous = by_task.get(task_id)
            if previous is None or sort_key >= previous[0]:
                by_task[task_id] = (sort_key, summary)
        return {task_id: summary for task_id, (_, summary) in by_task.items()}

    def review(
        self,
        task_id: str,
        *,
        decision: str,
        failure_reason: str | None = None,
    ) -> TaskInfo:
        normalized_decision = decision.strip().lower()
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"Task not found in roadmap: {task_id}")

        if normalized_decision in {"accept", "accepted", "approve", "approved", "done"}:
            if task.status is TaskStatus.ACCEPTED:
                return task
            if task.status is not TaskStatus.COMPLETED:
                raise ValueError(f"Cannot accept task from status {task.status.value}")
            task = self.update(task_id, status=TaskStatus.ACCEPTED)
            task_workflow = getattr(self.facade.orchestrator, "task_workflow", None)
            if task_workflow is not None:
                task_workflow.record_review(task, decision="accepted")
            return task

        if normalized_decision in {"needs_input", "awaiting_input"}:
            return task

        if normalized_decision in {"reject", "rejected", "retry", "needs_changes"}:
            review_decision = "rejected" if normalized_decision in {"reject", "rejected"} else "retry"
            default_reason = (
                "Gatekeeper rejected the task"
                if review_decision == "rejected"
                else "Gatekeeper requested changes"
            )
            if task.status is TaskStatus.COMPLETED:
                task = self.update(
                    task_id,
                    status=TaskStatus.FAILED,
                    failure_reason=failure_reason or default_reason,
                )
            task_workflow = getattr(self.facade.orchestrator, "task_workflow", None)
            if task_workflow is not None:
                task_workflow.record_review(task, decision=review_decision, reason=failure_reason or default_reason)
            return task

        if normalized_decision in {"escalate", "escalated"}:
            if task.status is TaskStatus.ESCALATED:
                return task
            if task.status is TaskStatus.COMPLETED:
                task = self.update(
                    task_id,
                    status=TaskStatus.FAILED,
                    failure_reason=failure_reason or "Gatekeeper escalated the task",
                )
            if task.status is TaskStatus.FAILED and task.can_transition_to(TaskStatus.ESCALATED):
                task = self.update(task_id, status=TaskStatus.ESCALATED)
                task_workflow = getattr(self.facade.orchestrator, "task_workflow", None)
                if task_workflow is not None:
                    task_workflow.record_review(
                        task,
                        decision="escalated",
                        reason=failure_reason or "Gatekeeper escalated the task",
                    )
                return task
            return task

        raise ValueError(f"Unsupported Gatekeeper review decision: {decision}")

    def queue_retry(
        self,
        task_id: str,
        *,
        failure_reason: str,
        prompt: str | None = None,
        acceptance_criteria: Sequence[str] | None = None,
    ) -> TaskInfo:
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"Task not found in roadmap: {task_id}")

        if prompt is not None:
            task = self.update(task_id, prompt=prompt)
        if acceptance_criteria is not None:
            task = self.update(task_id, acceptance_criteria=acceptance_criteria)

        if task.status is TaskStatus.COMPLETED:
            task = self.update(task_id, status=TaskStatus.FAILED, failure_reason=failure_reason)

        if task.status is TaskStatus.FAILED:
            next_status = TaskStatus.QUEUED if task.can_transition_to(TaskStatus.QUEUED) else TaskStatus.ESCALATED
            task = self.update(task_id, status=next_status)

        if task.status not in {TaskStatus.QUEUED, TaskStatus.ESCALATED}:
            raise ValueError(f"Cannot mark task for retry from status {task.status.value}")
        return task


@dataclass(frozen=True)
class QuestionAPI:
    """Stable structured-question surface."""

    facade: "OrchestratorFacade"

    @property
    def _service(self) -> object:
        return self.facade.orchestrator.question_service

    def get(self, question_id: str) -> QuestionRecord | None:
        getter = getattr(self._service, "get", None)
        if callable(getter):
            return getter(question_id)
        for record in self.list():
            if record.question_id == question_id:
                return record
        return None

    def list(self) -> list[QuestionRecord]:
        return self._service.records()

    def pending(self) -> list[QuestionRecord]:
        return self._service.pending_records()

    def current(self) -> QuestionRecord | None:
        current_record = getattr(self._service, "current_record", None)
        if callable(current_record):
            return current_record()
        current_question = getattr(self._service, "current_question", None)
        if callable(current_question):
            question_text = current_question()
            if question_text is None:
                return None
            for record in self.pending():
                if record.text == question_text:
                    return record
        pending = self.pending()
        return pending[0] if pending else None

    def ask(
        self,
        text: str,
        *,
        source_agent_id: str | None = None,
        source_role: str = "gatekeeper",
        source_run_id: str | None = None,
        priority: QuestionPriority = QuestionPriority.BLOCKING,
    ) -> QuestionRecord:
        return self._service.ask(
            text,
            source_agent_id=source_agent_id,
            source_role=source_role,
            source_run_id=source_run_id,
            priority=priority,
        )

    async def answer(
        self,
        answer: str,
        *,
        question_id: str | None = None,
    ) -> QuestionAnswerResult:
        question = self.current() if question_id is None else self.get(question_id)
        if question is None:
            raise ValueError("No pending Gatekeeper question to answer")
        if not question.is_pending():
            raise ValueError(f"Question is not pending: {question.question_id}")
        result = await self._service.answer(answer, question=question.text)
        gatekeeper_run = self.facade._run_snapshot_from_gatekeeper_result(result)
        resolved = self._service.resolve(
            question.question_id,
            answer=answer,
            resolved_by_run_id=gatekeeper_run.run_id,
        )
        return QuestionAnswerResult(question=resolved, gatekeeper_run=gatekeeper_run)

    def resolve(self, question_id: str, *, answer: str | None = None) -> QuestionRecord:
        return self._service.resolve(question_id, answer=answer)

    def sync_pending(
        self,
        questions: Sequence[str],
        *,
        source_agent_id: str | None = None,
        source_role: str = "gatekeeper",
        source_run_id: str | None = None,
    ) -> list[QuestionRecord]:
        return self._service.sync_pending(
            questions,
            source_agent_id=source_agent_id,
            source_role=source_role,
            source_run_id=source_run_id,
        )


@dataclass(frozen=True)
class WorkflowAPI:
    """Stable workflow-layer read/control surface."""

    facade: "OrchestratorFacade"

    def snapshot(self) -> WorkflowSnapshot:
        state_store = self.facade.orchestrator.state_store
        execution_mode = self.facade.orchestrator.execution_mode
        return WorkflowSnapshot(
            status=state_store.status,
            execution_mode=execution_mode.value if isinstance(execution_mode, RoadmapExecutionMode) else execution_mode,
            user_input_banner=state_store.user_input_banner(),
            notification_bell_enabled=state_store.notification_bell_enabled(),
        )

    def status(self) -> OrchestratorStatus:
        return self.facade.orchestrator.state_store.status

    def pause(self) -> None:
        if self.status() is OrchestratorStatus.PAUSED:
            return
        self.facade.transition_workflow_state(OrchestratorStatus.PAUSED)

    def resume(self) -> None:
        current = self.status()
        if current is OrchestratorStatus.EXECUTING:
            return
        if current is not OrchestratorStatus.PAUSED:
            raise ValueError(f"Cannot resume workflow from {current.value}")
        self.facade.transition_workflow_state(OrchestratorStatus.EXECUTING)

    def end_planning(self) -> OrchestratorStatus:
        self.facade.orchestrator.workflow_service.begin_execution_if_needed()
        self.facade.orchestrator.state_store.refresh()
        return self.status()

    async def execute_next_task(self) -> TaskExecutionResult | None:
        run_next_task = getattr(self.facade.orchestrator, "run_next_task", None)
        if not callable(run_next_task):
            raise AttributeError("Lifecycle does not support task execution")
        return await run_next_task()

    async def execute_until_blocked(self) -> list[TaskExecutionResult]:
        run_until_blocked = getattr(self.facade.orchestrator, "run_until_blocked", None)
        if not callable(run_until_blocked):
            raise AttributeError("Lifecycle does not support workflow execution")
        return await run_until_blocked()


@dataclass(frozen=True)
class GatekeeperAPI:
    """Optional thin Gatekeeper convenience alias."""

    facade: "OrchestratorFacade"

    async def submit(self, text: str) -> AgentRunSnapshot:
        result = await self.facade.orchestrator.submit_gatekeeper_message(text)
        return self.facade._run_snapshot_from_gatekeeper_result(result)


@dataclass(frozen=True)
class OrchestratorSnapshot:
    """Stable read model for orchestrator-backed consumers."""

    status: OrchestratorStatus
    pending_questions: tuple[str, ...]
    question_records: tuple[QuestionRecord, ...]
    roadmap: RoadmapDocument | None
    consensus: ConsensusDocument | None
    consensus_path: Path | None
    roles: tuple[AgentRoleSnapshot, ...]
    instances: tuple[AgentInstanceSnapshot, ...]
    execution_mode: RoadmapExecutionMode | None
    user_input_banner: str
    notification_bell_enabled: bool
    questions: tuple[QuestionRecord, ...]
    workflow: WorkflowSnapshot
    documents: DocumentSnapshot

class OrchestratorFacade:
    """Single entry point for orchestrator-backed app operations."""

    def __init__(self, orchestrator: Orchestrator) -> None:
        self.orchestrator = orchestrator
        self.workflow = WorkflowAPI(self)
        self.documents = DocumentAPI(self)
        self.questions = QuestionAPI(self)
        self.roles = RoleAPI(self)
        self.instances = InstanceAPI(self)
        self.runs = RunAPI(self)
        self.tasks = TaskAPI(self)
        self.gatekeeper = GatekeeperAPI(self)

    @staticmethod
    def _task_summary_timestamp(record: AgentRunRecord) -> float:
        if record.lifecycle.started_at is not None:
            return float(record.lifecycle.started_at.timestamp())
        if record.lifecycle.finished_at is not None:
            return float(record.lifecycle.finished_at.timestamp())
        return 0.0

    def _instance_snapshot_for_agent(self, agent_id: str) -> AgentInstanceSnapshot | None:
        manager = getattr(self.orchestrator, "agent_manager", None)
        if manager is None:
            return None
        return manager.get_instance_snapshot(agent_id)

    def _run_snapshot_from_record(
        self,
        record: AgentRunRecord,
        *,
        runtime_state: str | None = None,
        active: bool | None = None,
        done: bool | None = None,
        awaiting_input: bool | None = None,
        has_handle: bool | None = None,
        input_requests: Sequence[object] | None = None,
        provider_thread_id: str | None = None,
        provider_thread_path: str | None = None,
        resume_cursor: dict[str, Any] | None = None,
    ) -> AgentRunSnapshot:
        instance_snapshot = self._instance_snapshot_for_agent(record.identity.agent_id)
        matched_instance = (
            instance_snapshot is not None and instance_snapshot.identity.run_id == record.identity.run_id
        )
        provider_handle = ProviderResumeHandle.from_provider_metadata(record.provider)

        if runtime_state is None:
            runtime_state = (
                instance_snapshot.runtime.state
                if matched_instance
                else record.lifecycle.status.value
            )
        if active is None:
            active = instance_snapshot.runtime.active if matched_instance else record.lifecycle.status not in AgentRunRecord.TERMINAL_STATUSES
        if done is None:
            done = instance_snapshot.runtime.done if matched_instance else record.lifecycle.status in AgentRunRecord.TERMINAL_STATUSES
        if awaiting_input is None:
            awaiting_input = (
                instance_snapshot.runtime.awaiting_input
                if matched_instance
                else record.lifecycle.status.value == "awaiting_input"
            )
        if has_handle is None:
            has_handle = instance_snapshot.runtime.has_handle if matched_instance else False
        if input_requests is None:
            input_requests = (
                tuple(instance_snapshot.runtime.input_requests)
                if matched_instance
                else ()
            )
        if provider_thread_id is None:
            provider_thread_id = (
                instance_snapshot.provider.thread_id
                if matched_instance
                else (provider_handle.thread_id if provider_handle is not None else None)
            )
        if provider_thread_path is None:
            provider_thread_path = (
                instance_snapshot.provider.thread_path
                if matched_instance
                else (provider_handle.thread_path if provider_handle is not None else None)
            )
        if resume_cursor is None:
            resume_cursor = (
                instance_snapshot.provider.resume_cursor
                if matched_instance
                else (provider_handle.resume_cursor if provider_handle is not None else None)
            )

        payload = parse_role_result(record.outcome.role_result)
        identity = AgentSnapshotIdentity(
            agent_id=record.identity.agent_id,
            task_id=record.identity.task_id,
            role=record.identity.role,
            run_id=record.identity.run_id,
            scope_type=instance_snapshot.identity.scope_type if instance_snapshot is not None else None,
            scope_id=instance_snapshot.identity.scope_id if instance_snapshot is not None else None,
        )
        lifecycle = RunLifecycleSnapshot(
            status=record.lifecycle.status.value,
            pid=record.lifecycle.pid,
            started_at=record.lifecycle.started_at,
            finished_at=record.lifecycle.finished_at,
        )
        runtime = RunRuntimeSnapshot(
            state=runtime_state,
            active=active,
            done=done,
            awaiting_input=awaiting_input,
            has_handle=has_handle,
            input_requests=tuple(input_requests),
        )
        workspace = RunWorkspaceSnapshot(
            branch=record.context.branch,
            worktree_path=record.context.worktree_path,
        )
        provider = RunProviderSnapshot(
            kind=record.provider.kind,
            transport=record.provider.transport,
            runtime_mode=record.provider.runtime_mode,
            provider_thread_id=provider_thread_id,
            thread_path=provider_thread_path,
            resume_cursor=resume_cursor,
            native_event_log=record.provider.native_event_log,
            canonical_event_log=record.provider.canonical_event_log,
        )
        envelope = RunEnvelope(
            state=runtime_state,
            summary=record.outcome.summary,
            error=record.outcome.error,
            input_requests=tuple(input_requests),
            canonical_event_log=record.provider.canonical_event_log,
            native_event_log=record.provider.native_event_log,
            provider_thread_id=provider_thread_id,
            provider_thread_path=provider_thread_path,
            resume_cursor=resume_cursor,
        )
        return AgentRunSnapshot(
            run_id=record.identity.run_id,
            agent_id=record.identity.agent_id,
            task_id=record.identity.task_id,
            role=record.identity.role,
            lifecycle=lifecycle,
            runtime=runtime,
            workspace=workspace,
            provider=provider,
            envelope=envelope,
            payload=payload,
            identity=identity,
            context=AgentRunContextSnapshot(
                branch=record.context.branch,
                worktree_path=record.context.worktree_path,
                prompt_used=record.context.prompt_used,
                skills_loaded=tuple(record.context.skills_loaded),
            ),
            outcome=AgentRunOutcomeSnapshot(
                exit_code=record.outcome.exit_code,
                summary=record.outcome.summary,
                error=record.outcome.error,
                role_result=payload,
            ),
            retry=AgentRunRetrySnapshot(
                retry_count=record.retry.retry_count,
                max_retries=record.retry.max_retries,
            ),
            state=runtime_state,
            summary=record.outcome.summary,
            error=record.outcome.error,
        )

    def _run_snapshot_from_gatekeeper_result(self, result: GatekeeperRunResult) -> AgentRunSnapshot:
        if not isinstance(result, NormalizedRunResult):
            raise TypeError("Expected a normalized Gatekeeper run result")
        provider_thread = result.provider_thread
        return self._run_snapshot_from_record(
            result.agent_record,
            runtime_state=result.state.value,
            active=result.awaiting_input,
            done=not result.awaiting_input and result.agent_record.lifecycle.status in AgentRunRecord.TERMINAL_STATUSES,
            awaiting_input=result.awaiting_input,
            has_handle=result.awaiting_input,
            input_requests=tuple(result.input_requests),
            provider_thread_id=provider_thread.thread_id,
            provider_thread_path=provider_thread.thread_path,
            resume_cursor=provider_thread.resume_cursor,
        )

    def _run_snapshot_from_execution_result(self, result: RuntimeExecutionResult) -> AgentRunSnapshot:
        runtime_state = result.state.value if result.state is not None else result.agent_record.lifecycle.status.value
        return self._run_snapshot_from_record(
            result.agent_record,
            runtime_state=runtime_state,
            active=result.awaiting_input or result.agent_record.lifecycle.status not in AgentRunRecord.TERMINAL_STATUSES,
            done=not result.awaiting_input and result.agent_record.lifecycle.status in AgentRunRecord.TERMINAL_STATUSES,
            awaiting_input=result.awaiting_input,
            has_handle=result.awaiting_input,
            input_requests=tuple(result.input_requests),
            provider_thread_id=result.provider_thread_id,
            provider_thread_path=result.provider_thread_path,
            resume_cursor=result.provider_resume_cursor,
        )

    @property
    def roadmap_document(self) -> RoadmapDocument | None:
        return self.orchestrator.roadmap_document

    @property
    def execution_mode(self) -> RoadmapExecutionMode | None:
        return self.orchestrator.execution_mode

    def snapshot(self) -> OrchestratorSnapshot:
        state_store = self.orchestrator.state_store
        workflow = self.workflow.snapshot()
        documents = self.documents.snapshot()
        question_records = tuple(self.questions.list())
        return OrchestratorSnapshot(
            status=state_store.status,
            pending_questions=tuple(record.text for record in self.questions.pending()),
            question_records=question_records,
            roadmap=documents.roadmap,
            consensus=documents.consensus,
            consensus_path=documents.consensus_path,
            roles=tuple(self.roles.list()),
            instances=tuple(self.instances.list()),
            execution_mode=self.orchestrator.execution_mode,
            user_input_banner=workflow.user_input_banner,
            notification_bell_enabled=workflow.notification_bell_enabled,
            questions=question_records,
            workflow=workflow,
            documents=documents,
        )

    def get_workflow_status(self) -> OrchestratorStatus:
        return self.workflow.status()

    def get_consensus_document(self) -> ConsensusDocument | None:
        return self.documents.consensus()

    def get_roadmap(self) -> RoadmapDocument | None:
        return self.documents.roadmap()

    def get_consensus_source_path(self) -> Path | None:
        return self.documents.consensus_source_path()

    def list_question_records(self) -> list[QuestionRecord]:
        return self.questions.list()

    def list_pending_question_records(self) -> list[QuestionRecord]:
        return self.questions.pending()

    def get_task(self, task_id: str) -> TaskInfo | None:
        return self.tasks.get(task_id)

    def add_task(self, task: TaskInfo, *, index: int | None = None) -> TaskInfo:
        return self.tasks.add(task, index=index)

    def update_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        acceptance_criteria: Sequence[str] | None = None,
        status: TaskStatus | str | None = None,
        agent_role: str | None = None,
        branch: str | None = None,
        retry_count: int | None = None,
        max_retries: int | None = None,
        prompt: str | None = None,
        skills: Sequence[str] | None = None,
        dependencies: Sequence[str] | None = None,
        priority: int | None = None,
        failure_reason: str | None = None,
    ) -> TaskInfo:
        return self.tasks.update(
            task_id,
            title=title,
            acceptance_criteria=acceptance_criteria,
            status=status,
            agent_role=agent_role,
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
        return self.tasks.reorder(ordered_task_ids)

    def replace_roadmap(self, *, tasks: list[TaskInfo], project: str | None = None) -> RoadmapDocument:
        return self.documents.replace_roadmap(tasks=tasks, project=project)

    def update_consensus(
        self,
        *,
        status: ConsensusStatus | str | None = None,
        context: str | None = None,
    ) -> ConsensusDocument:
        return self.documents.update_consensus(status=status, context=context)

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
        return self.tasks.summaries()

    def get_user_input_banner(self) -> str:
        return self.orchestrator.state_store.user_input_banner()

    def is_notification_bell_enabled(self) -> bool:
        return self.orchestrator.state_store.notification_bell_enabled()

    def write_consensus_document(self, document: ConsensusDocument) -> ConsensusDocument:
        return self.documents.write_consensus(document)

    async def submit_gatekeeper_message(self, text: str) -> GatekeeperRunResult:
        return await self.orchestrator.submit_gatekeeper_message(text)

    async def answer_pending_question(
        self,
        answer: str,
        *,
        question: str | None = None,
    ) -> GatekeeperRunResult:
        return await self.orchestrator.question_service.answer(answer, question=question)

    async def execute_next_task(self) -> TaskExecutionResult | None:
        return await self.workflow.execute_next_task()

    async def execute_until_blocked(self) -> list[TaskExecutionResult]:
        return await self.workflow.execute_until_blocked()

    def pause_workflow(self) -> None:
        self.workflow.pause()

    def resume_workflow(self) -> None:
        self.workflow.resume()

    def end_planning_phase(self) -> OrchestratorStatus:
        return self.workflow.end_planning()

    def review_task_outcome(self, task_id: str, *, decision: str, failure_reason: str | None = None) -> TaskInfo:
        return self.tasks.review(task_id, decision=decision, failure_reason=failure_reason)

    def mark_task_for_retry(
        self,
        task_id: str,
        *,
        failure_reason: str,
        prompt: str | None = None,
        acceptance_criteria: Sequence[str] | None = None,
    ) -> TaskInfo:
        return self.tasks.queue_retry(
            task_id,
            failure_reason=failure_reason,
            prompt=prompt,
            acceptance_criteria=acceptance_criteria,
        )

    def list_pending_questions(self) -> list[str]:
        return [record.text for record in self.questions.pending()]

    def get_current_pending_question(self) -> str | None:
        record = self.questions.current()
        return record.text if record is not None else None

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
            self.orchestrator.state_store.refresh()
            return
        self.orchestrator.state_store.transition_to(next_status)
        self.orchestrator.state_store.refresh()

    def _sync_consensus_status(self, next_status: OrchestratorStatus) -> None:
        target_status = _WORKFLOW_TO_CONSENSUS.get(next_status)
        if target_status is not None:
            self.orchestrator.consensus_service.set_status(target_status)


def _read_canonical_event_log(path: str | Path | None) -> list[CanonicalEvent]:
    if path is None:
        return []

    log_path = Path(path)
    if not log_path.exists():
        return []

    events: list[CanonicalEvent] = []
    try:
        with log_path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue

                event_data = payload.get("data")
                event: dict[str, Any] = dict(event_data) if isinstance(event_data, dict) else {}
                event["type"] = str(payload.get("event") or event.get("type") or "event")

                timestamp = payload.get("timestamp")
                if isinstance(timestamp, str):
                    event["timestamp"] = timestamp
                events.append(event)
    except OSError:
        return []
    return events


__all__ = [
    "OrchestratorFacade",
    "OrchestratorSnapshot",
]
