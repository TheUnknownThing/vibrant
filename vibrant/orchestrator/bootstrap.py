"""Bootstrap and composition root for the redesigned orchestrator."""

from __future__ import annotations

from collections import deque
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vibrant.agents.gatekeeper import Gatekeeper
from vibrant.config import DEFAULT_CONFIG_DIR, RoadmapExecutionMode, VibrantConfig, find_project_root, load_config
from vibrant.consensus.roadmap import RoadmapDocument
from vibrant.models.agent import AgentRecord
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus
from vibrant.models.task import TaskInfo
from vibrant.project_init import ensure_project_files
from vibrant.providers.base import CanonicalEvent
from vibrant.providers.registry import resolve_provider_adapter

from .binding import AgentSessionBindingService
from .control_plane import OrchestratorControlPlane
from .conversation import ConversationStore, ConversationStreamService
from .execution.coordinator import ExecutionCoordinator
from .gatekeeper import GatekeeperLifecycleService
from .mcp import OrchestratorFastMCPHost, OrchestratorMCPServer
from .review import ReviewControlService
from .runtime import AgentRuntimeService
from .stores import (
    AgentRecordStore,
    AttemptStore,
    ConsensusStore,
    QuestionStore,
    ReviewTicketStore,
    RoadmapStore,
    WorkflowStateStore,
)
from .types import (
    GatekeeperSessionSnapshot,
    QuestionPriority,
    QuestionRecord,
    ReviewResolutionCommand,
    ReviewResolutionRecord,
    ReviewTicket,
    TaskResult,
    TaskState,
    WorkflowSnapshot,
    WorkflowStatus,
)
from .workflow import WorkflowPolicyService
from .workspace import WorkspaceService


@dataclass(slots=True)
class Orchestrator:
    """Composed orchestrator root."""

    project_root: Path
    vibrant_dir: Path
    config: VibrantConfig
    workflow_state_store: WorkflowStateStore
    attempt_store: AttemptStore
    question_store: QuestionStore
    consensus_store: ConsensusStore
    roadmap_store: RoadmapStore
    review_ticket_store: ReviewTicketStore
    agent_record_store: AgentRecordStore
    conversation_store: ConversationStore
    conversation_stream: ConversationStreamService
    runtime_service: AgentRuntimeService
    workspace_service: WorkspaceService
    workflow_policy: WorkflowPolicyService
    review_control: ReviewControlService
    execution_coordinator: ExecutionCoordinator
    gatekeeper_lifecycle: GatekeeperLifecycleService
    control_plane: OrchestratorControlPlane
    mcp_server: OrchestratorMCPServer
    mcp_host: OrchestratorFastMCPHost
    session_binding: AgentSessionBindingService
    gatekeeper: Gatekeeper
    adapter_factory: Any
    on_canonical_event: Any | None = None
    _recent_events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=200), repr=False)

    @classmethod
    def load(
        cls,
        project_root: str | Path,
        *,
        gatekeeper: Gatekeeper | None = None,
        adapter_factory: Any | None = None,
        on_canonical_event: Any | None = None,
        **_: Any,
    ) -> "Orchestrator":
        root = find_project_root(project_root)
        ensure_project_files(root)
        vibrant_dir = root / DEFAULT_CONFIG_DIR
        config = load_config(start_path=root)
        resolved_adapter_factory = adapter_factory or resolve_provider_adapter(config.provider_kind)

        workflow_state_store = WorkflowStateStore(vibrant_dir / "state.json")
        attempt_store = AttemptStore(vibrant_dir / "attempts.json")
        question_store = QuestionStore(vibrant_dir / "questions.json")
        consensus_store = ConsensusStore(vibrant_dir / "consensus.md", project_name=root.name)
        roadmap_store = RoadmapStore(vibrant_dir / "roadmap.md", project_name=root.name)
        review_ticket_store = ReviewTicketStore(vibrant_dir / "reviews.json")
        agent_record_store = AgentRecordStore(vibrant_dir / "agents")
        conversation_store = ConversationStore(vibrant_dir)
        conversation_stream = ConversationStreamService(conversation_store)
        runtime_service = AgentRuntimeService()
        workspace_service = WorkspaceService(
            project_root=root,
            worktree_root=config.worktree_directory,
        )
        workflow_policy = WorkflowPolicyService(
            state_store=workflow_state_store,
            roadmap_store=roadmap_store,
            attempt_store=attempt_store,
            question_store=question_store,
            agent_store=agent_record_store,
        )
        review_control = ReviewControlService(
            review_ticket_store=review_ticket_store,
            workflow_policy=workflow_policy,
            roadmap_store=roadmap_store,
            workspace_service=workspace_service,
            attempt_store=attempt_store,
        )
        execution_coordinator = ExecutionCoordinator(
            project_root=root,
            config=config,
            consensus_store=consensus_store,
            roadmap_store=roadmap_store,
            attempt_store=attempt_store,
            agent_store=agent_record_store,
            workspace_service=workspace_service,
            runtime_service=runtime_service,
            conversation_stream=conversation_stream,
            workflow_policy=workflow_policy,
            adapter_factory=resolved_adapter_factory,
        )
        resolved_gatekeeper = gatekeeper or Gatekeeper(root)
        gatekeeper_lifecycle = GatekeeperLifecycleService(
            root,
            runtime_service=runtime_service,
            conversation_service=conversation_stream,
            gatekeeper=resolved_gatekeeper,
            binding_service=None,
            mcp_host=None,
            session_loader=lambda: workflow_state_store.load().gatekeeper_session,
            session_saver=workflow_state_store.update_gatekeeper_session,
            on_record_updated=agent_record_store.upsert,
        )
        control_plane = OrchestratorControlPlane(
            workflow_state_store=workflow_state_store,
            question_store=question_store,
            attempt_store=attempt_store,
            agent_store=agent_record_store,
            gatekeeper_lifecycle=gatekeeper_lifecycle,
            conversation_stream=conversation_stream,
            workflow_policy=workflow_policy,
            roadmap_store=roadmap_store,
            review_control=review_control,
        )

        orchestrator = cls(
            project_root=root,
            vibrant_dir=vibrant_dir,
            config=config,
            workflow_state_store=workflow_state_store,
            attempt_store=attempt_store,
            question_store=question_store,
            consensus_store=consensus_store,
            roadmap_store=roadmap_store,
            review_ticket_store=review_ticket_store,
            agent_record_store=agent_record_store,
            conversation_store=conversation_store,
            conversation_stream=conversation_stream,
            runtime_service=runtime_service,
            workspace_service=workspace_service,
            workflow_policy=workflow_policy,
            review_control=review_control,
            execution_coordinator=execution_coordinator,
            gatekeeper_lifecycle=gatekeeper_lifecycle,
            control_plane=control_plane,
            mcp_server=None,  # type: ignore[arg-type]
            mcp_host=None,  # type: ignore[arg-type]
            session_binding=None,  # type: ignore[arg-type]
            gatekeeper=resolved_gatekeeper,
            adapter_factory=resolved_adapter_factory,
            on_canonical_event=on_canonical_event,
        )
        orchestrator.mcp_server = OrchestratorMCPServer(orchestrator)
        orchestrator.mcp_host = OrchestratorFastMCPHost(orchestrator.mcp_server)
        orchestrator.session_binding = AgentSessionBindingService(
            mcp_server=orchestrator.mcp_server,
            mcp_host=orchestrator.mcp_host,
        )
        gatekeeper_lifecycle.binding_service = orchestrator.session_binding
        gatekeeper_lifecycle.mcp_host = orchestrator.mcp_host
        runtime_service.subscribe_canonical_events(orchestrator._record_runtime_event)
        runtime_service.subscribe_canonical_events(conversation_stream.ingest_canonical)
        orchestrator.refresh()
        return orchestrator

    @property
    def roadmap_path(self) -> Path:
        return self.roadmap_store.path

    @property
    def consensus_path(self) -> Path:
        return self.consensus_store.path

    @property
    def execution_mode(self) -> RoadmapExecutionMode:
        return self.config.execution_mode

    @property
    def binding_service(self) -> AgentSessionBindingService:
        return self.session_binding

    @property
    def gatekeeper_busy(self) -> bool:
        return self.gatekeeper_lifecycle.busy

    def refresh(self) -> RoadmapDocument:
        self.config = load_config(start_path=self.project_root)
        return self.roadmap_store.load()

    async def submit_user_message(self, text: str):
        return await self.control_plane.submit_user_message(text)

    async def answer_user_decision(self, question_id: str, answer: str):
        return await self.control_plane.answer_user_decision(question_id, answer)

    async def start_execution(self) -> WorkflowSnapshot:
        self.workflow_state_store.update_workflow_status(WorkflowStatus.EXECUTING)
        self.consensus_store.set_status_projection(ConsensusStatus.EXECUTING)
        return self.control_plane.snapshot()

    async def pause_workflow(self) -> WorkflowSnapshot:
        self.workflow_state_store.update_workflow_status(WorkflowStatus.PAUSED)
        self.consensus_store.set_status_projection(ConsensusStatus.PAUSED)
        return self.control_plane.snapshot()

    async def resume_workflow(self) -> WorkflowSnapshot:
        self.workflow_state_store.update_workflow_status(WorkflowStatus.EXECUTING)
        self.consensus_store.set_status_projection(ConsensusStatus.EXECUTING)
        return self.control_plane.snapshot()

    async def restart_gatekeeper(self, reason: str | None = None) -> GatekeeperSessionSnapshot:
        return await self.control_plane.restart_gatekeeper(reason)

    async def stop_gatekeeper(self) -> GatekeeperSessionSnapshot:
        return await self.control_plane.stop_gatekeeper()

    async def run_until_blocked(self) -> list[TaskResult]:
        results: list[TaskResult] = []
        while True:
            result = await self.run_next_task()
            if result is None:
                break
            results.append(result)
            if result.outcome in {"awaiting_user", "review_pending", "failed"}:
                break
        return results

    async def run_next_task(self) -> TaskResult | None:
        leases = self.workflow_policy.select_next(limit=1)
        if not leases:
            self.workflow_policy.maybe_complete()
            return None
        lease = leases[0]
        attempt = await self.execution_coordinator.start_attempt(lease)
        completion = await self.execution_coordinator.await_attempt_completion(attempt.attempt_id)
        if completion.status == "awaiting_input":
            return TaskResult(
                task_id=completion.task_id,
                outcome="awaiting_user",
                summary=completion.summary,
                error=completion.error,
            )

        workspace = self.workspace_service.get_workspace(task_id=completion.task_id, workspace_id=completion.workspace_ref)
        diff = self.workspace_service.collect_review_diff(workspace)
        self.review_control.create_ticket(completion, diff)
        return TaskResult(
            task_id=completion.task_id,
            outcome="review_pending",
            summary=completion.summary,
            error=completion.error,
            worktree_path=workspace.path,
        )

    def snapshot(self) -> WorkflowSnapshot:
        return self.control_plane.snapshot()

    def list_recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        return list(self._recent_events)[-limit:]

    async def shutdown(self) -> None:
        """Stop transport services owned by the orchestrator."""

        await self.mcp_host.stop()

    def get_consensus_document(self) -> ConsensusDocument | None:
        return self.consensus_store.load()

    def get_roadmap(self) -> RoadmapDocument:
        return self.roadmap_store.load()

    def get_task(self, task_id: str) -> TaskInfo | None:
        return self.roadmap_store.get_task(task_id)

    def get_workflow_status(self) -> WorkflowStatus:
        return self.workflow_state_store.load().workflow_status

    def list_agent_records(self) -> list[AgentRecord]:
        return self.agent_record_store.list()

    def list_active_agents(self) -> list[AgentRecord]:
        return self.agent_record_store.list_active()

    def list_active_attempts(self):
        return self.attempt_store.list_active()

    def get_review_ticket(self, ticket_id: str) -> ReviewTicket | None:
        return self.review_control.get_ticket(ticket_id)

    def list_pending_review_tickets(self) -> list[ReviewTicket]:
        return self.review_control.list_pending()

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

    def withdraw_question(self, question_id: str, *, reason: str | None = None) -> QuestionRecord:
        return self.question_store.withdraw(question_id, reason=reason)

    def update_consensus(
        self,
        *,
        context: str | None = None,
        status: str | None = None,
    ) -> ConsensusDocument:
        if context is not None:
            document = self.consensus_store.update_context(context)
        else:
            document = self.consensus_store.load() or ConsensusDocument(project=self.project_root.name)
        if status is not None:
            document.status = ConsensusStatus(status.upper())
            document = self.consensus_store.write(document)
        return document

    def append_decision(self, **kwargs: Any) -> ConsensusDocument:
        return self.consensus_store.append_decision(**kwargs)

    def add_task(self, task: TaskInfo, *, index: int | None = None):
        self.roadmap_store.add_task(task, index=index)
        created = self.roadmap_store.get_task(task.id)
        if created is None:
            raise KeyError(task.id)
        return created

    def update_task_definition(self, task_id: str, **patch: Any):
        return self.roadmap_store.update_task_definition(task_id, **patch)

    def reorder_tasks(self, task_ids: list[str]):
        return self.roadmap_store.reorder_tasks(task_ids)

    def replace_roadmap(self, *, tasks: list[TaskInfo], project: str | None = None):
        return self.roadmap_store.replace(tasks=tasks, project=project or self.project_root.name)

    def end_planning_phase(self):
        self.workflow_state_store.update_workflow_status(WorkflowStatus.EXECUTING)
        self.consensus_store.set_status_projection(ConsensusStatus.EXECUTING)
        return self.control_plane.snapshot()

    def accept_review_ticket(self, ticket_id: str) -> ReviewResolutionRecord:
        return self.review_control.resolve(ticket_id, ReviewResolutionCommand(decision="accept"))

    def retry_review_ticket(
        self,
        ticket_id: str,
        *,
        failure_reason: str,
        prompt_patch: str | None = None,
        acceptance_patch: list[str] | None = None,
    ) -> ReviewResolutionRecord:
        return self.review_control.resolve(
            ticket_id,
            ReviewResolutionCommand(
                decision="retry",
                failure_reason=failure_reason,
                prompt_patch=prompt_patch,
                acceptance_patch=acceptance_patch,
            ),
        )

    def escalate_review_ticket(self, ticket_id: str, *, reason: str) -> ReviewResolutionRecord:
        return self.review_control.resolve(
            ticket_id,
            ReviewResolutionCommand(decision="escalate", failure_reason=reason),
        )

    def set_pending_questions(
        self,
        questions: list[str],
        *,
        source_agent_id: str | None = None,
        source_role: str = "gatekeeper",
    ) -> list[QuestionRecord]:
        existing_pending = {record.text: record for record in self.question_store.list_pending()}
        requested = {question.strip() for question in questions if question.strip()}

        for text, record in existing_pending.items():
            if text not in requested:
                self.question_store.withdraw(record.question_id, reason="Legacy pending-question sync removed it")

        created: list[QuestionRecord] = []
        for text in requested:
            existing = existing_pending.get(text)
            if existing is not None:
                created.append(existing)
                continue
            created.append(
                self.question_store.create(
                    text=text,
                    priority=QuestionPriority.BLOCKING,
                    source_role=source_role,
                    source_agent_id=source_agent_id,
                    source_conversation_id=None,
                    source_turn_id=None,
                    blocking_scope="planning",
                    task_id=None,
                )
            )
        return created

    def review_task_outcome(self, task_id: str, *, decision: str, failure_reason: str | None = None):
        review_kind = decision.strip().lower()
        active_attempt = self.attempt_store.get_active_by_task(task_id)
        if active_attempt is None:
            raise KeyError(f"No active attempt for task: {task_id}")

        if review_kind in {"accept", "accepted"}:
            self.workflow_policy.mark_task_accepted(task_id=task_id, attempt_id=active_attempt.attempt_id)
            return self.roadmap_store.get_task(task_id)
        if review_kind in {"retry", "rejected", "needs_changes"}:
            self.workflow_policy.requeue_task(task_id=task_id, attempt_id=active_attempt.attempt_id)
            return self.roadmap_store.get_task(task_id)
        if review_kind in {"escalate", "escalated"}:
            self.workflow_policy.mark_task_escalated(task_id=task_id, attempt_id=active_attempt.attempt_id)
            return self.roadmap_store.get_task(task_id)
        raise ValueError(f"Unsupported review decision: {decision}")

    def mark_task_for_retry(
        self,
        task_id: str,
        *,
        failure_reason: str,
        prompt: str | None = None,
        acceptance_criteria: list[str] | None = None,
    ):
        patch: dict[str, Any] = {}
        if prompt is not None:
            patch["prompt"] = prompt
        if acceptance_criteria is not None:
            patch["acceptance_criteria"] = acceptance_criteria
        if patch:
            self.roadmap_store.update_task_definition(task_id, patch)
        active_attempt = self.attempt_store.get_active_by_task(task_id)
        if active_attempt is not None:
            self.workflow_policy.requeue_task(task_id=task_id, attempt_id=active_attempt.attempt_id)
        return self.roadmap_store.record_task_state(task_id, TaskState.READY, failure_reason=failure_reason)

    async def _record_runtime_event(self, event: CanonicalEvent) -> None:
        self._recent_events.append(dict(event))
        if self.on_canonical_event is not None:
            result = self.on_canonical_event(event)
            if inspect.isawaitable(result):
                await result


def create_orchestrator(
    project_root: str | Path,
    *,
    gatekeeper: Gatekeeper | None = None,
    adapter_factory: Any | None = None,
    on_canonical_event: Any | None = None,
    **kwargs: Any,
) -> Orchestrator:
    """Build a fully wired orchestrator for one project."""

    return Orchestrator.load(
        project_root,
        gatekeeper=gatekeeper,
        adapter_factory=adapter_factory,
        on_canonical_event=on_canonical_event,
        **kwargs,
    )
