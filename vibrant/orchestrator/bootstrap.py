"""Bootstrap and composition root for the layered orchestrator."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from vibrant.agents.gatekeeper import Gatekeeper
from vibrant.config import DEFAULT_CONFIG_DIR, RoadmapExecutionMode, VibrantConfig, find_project_root, load_config
from vibrant.consensus.roadmap import RoadmapDocument
from vibrant.models.consensus import ConsensusDocument
from vibrant.models.task import TaskInfo
from vibrant.project_init import ensure_project_files
from vibrant.providers.registry import resolve_provider_adapter

from .basic.binding import AgentSessionBindingService
from .basic.conversation import ConversationStore, ConversationStreamService
from .basic.events import EventLogService
from .basic.runtime import AgentRuntimeService
from .basic.stores import (
    AgentInstanceStore,
    AgentRunStore,
    AttemptStore,
    ConsensusStore,
    QuestionStore,
    ReviewTicketStore,
    RoadmapStore,
    WorkflowStateStore,
)
from .basic.stores.gatekeeper_session import project_gatekeeper_session
from .basic.workspace import WorkspaceService
from .interface import BasicQueryAdapter, InterfaceControlPlane, OrchestratorBackend, PolicyCommandAdapter
from .interface.mcp import OrchestratorFastMCPHost, OrchestratorMCPServer
from .policy import GatekeeperLoopState
from .policy.gatekeeper_loop import GatekeeperLifecycleService, GatekeeperUserLoop
from .policy.task_loop import ExecutionCoordinator, TaskLoop
from .types import GatekeeperLifecycleStatus, GatekeeperSessionSnapshot, TaskResult, WorkflowSnapshot, WorkflowStatus


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
    agent_instance_store: AgentInstanceStore
    agent_run_store: AgentRunStore
    conversation_store: ConversationStore
    conversation_stream: ConversationStreamService
    runtime_service: AgentRuntimeService
    workspace_service: WorkspaceService
    binding_service: AgentSessionBindingService
    event_log: EventLogService
    gatekeeper_lifecycle: GatekeeperLifecycleService
    execution_coordinator: ExecutionCoordinator
    gatekeeper_loop: GatekeeperUserLoop
    task_loop: TaskLoop
    backend: OrchestratorBackend
    control_plane: InterfaceControlPlane
    mcp_server: OrchestratorMCPServer
    mcp_host: OrchestratorFastMCPHost
    gatekeeper: Gatekeeper
    adapter_factory: Any

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
        agent_instance_store = AgentInstanceStore(vibrant_dir / "agent-instances")
        agent_run_store = AgentRunStore(vibrant_dir / "agent-runs")
        conversation_store = ConversationStore(vibrant_dir)
        conversation_stream = ConversationStreamService(conversation_store)
        runtime_service = AgentRuntimeService()
        workspace_service = WorkspaceService(project_root=root, worktree_root=config.worktree_directory)
        _repair_runtime_state(
            workflow_state_store=workflow_state_store,
            agent_run_store=agent_run_store,
            agent_instance_store=agent_instance_store,
            runtime_service=runtime_service,
        )

        event_log = EventLogService(on_canonical_event=on_canonical_event)

        resolved_gatekeeper = gatekeeper or Gatekeeper(root)
        gatekeeper_lifecycle = GatekeeperLifecycleService(
            root,
            runtime_service=runtime_service,
            conversation_service=conversation_stream,
            gatekeeper=resolved_gatekeeper,
            binding_service=None,
            mcp_host=None,
            instance_store=agent_instance_store,
            run_store=agent_run_store,
            session_loader=lambda: workflow_state_store.load().gatekeeper_session,
            session_saver=workflow_state_store.update_gatekeeper_session,
        )
        execution_coordinator = ExecutionCoordinator(
            project_root=root,
            config=config,
            attempt_store=attempt_store,
            agent_instance_store=agent_instance_store,
            agent_run_store=agent_run_store,
            workspace_service=workspace_service,
            runtime_service=runtime_service,
            conversation_stream=conversation_stream,
            adapter_factory=resolved_adapter_factory,
        )
        gatekeeper_loop = GatekeeperUserLoop(
            project_name=root.name,
            workflow_state_store=workflow_state_store,
            agent_run_store=agent_run_store,
            attempt_store=attempt_store,
            question_store=question_store,
            consensus_store=consensus_store,
            roadmap_store=roadmap_store,
            conversation_service=conversation_stream,
            runtime_service=runtime_service,
            lifecycle=gatekeeper_lifecycle,
        )
        task_loop = TaskLoop(
            workflow_state_store=workflow_state_store,
            agent_run_store=agent_run_store,
            attempt_store=attempt_store,
            question_store=question_store,
            consensus_store=consensus_store,
            roadmap_store=roadmap_store,
            review_ticket_store=review_ticket_store,
            workspace_service=workspace_service,
            execution=execution_coordinator,
        )

        commands = PolicyCommandAdapter(
            gatekeeper_loop=gatekeeper_loop,
            task_loop=task_loop,
        )
        queries = BasicQueryAdapter(
            workflow_state_store=workflow_state_store,
            attempt_store=attempt_store,
            question_store=question_store,
            consensus_store=consensus_store,
            roadmap_store=roadmap_store,
            agent_instance_store=agent_instance_store,
            agent_run_store=agent_run_store,
            runtime_service=runtime_service,
            event_log=event_log,
            gatekeeper_loop=gatekeeper_loop,
            task_loop=task_loop,
        )
        backend = OrchestratorBackend(commands=commands, queries=queries)
        control_plane = InterfaceControlPlane(backend=backend)
        mcp_server = OrchestratorMCPServer(backend)
        mcp_host = OrchestratorFastMCPHost(mcp_server)
        session_binding = AgentSessionBindingService(
            mcp_server=mcp_server,
            mcp_host=mcp_host,
        )
        gatekeeper_lifecycle.binding_service = session_binding
        gatekeeper_lifecycle.mcp_host = mcp_host

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
            agent_instance_store=agent_instance_store,
            agent_run_store=agent_run_store,
            conversation_store=conversation_store,
            conversation_stream=conversation_stream,
            runtime_service=runtime_service,
            workspace_service=workspace_service,
            binding_service=session_binding,
            event_log=event_log,
            gatekeeper_lifecycle=gatekeeper_lifecycle,
            execution_coordinator=execution_coordinator,
            gatekeeper_loop=gatekeeper_loop,
            task_loop=task_loop,
            backend=backend,
            control_plane=control_plane,
            mcp_server=mcp_server,
            mcp_host=mcp_host,
            gatekeeper=resolved_gatekeeper,
            adapter_factory=resolved_adapter_factory,
        )
        runtime_service.subscribe_canonical_events(orchestrator.event_log.record_runtime_event)
        runtime_service.subscribe_canonical_events(conversation_stream.ingest_canonical)
        orchestrator.refresh()
        return orchestrator

    @property
    def agent_record_store(self) -> AgentRunStore:
        return self.agent_run_store

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
    def gatekeeper_busy(self) -> bool:
        return self.control_plane.gatekeeper_busy()

    def refresh(self) -> RoadmapDocument:
        self.config = load_config(start_path=self.project_root)
        return self.roadmap_store.load()

    async def submit_user_message(self, text: str):
        return await self.control_plane.submit_user_input(text)

    async def answer_user_decision(self, question_id: str, answer: str):
        return await self.control_plane.submit_user_input(answer, question_id=question_id)

    def start_execution(self) -> WorkflowSnapshot:
        return self.control_plane.start_execution()

    def pause_workflow(self) -> WorkflowSnapshot:
        return self.control_plane.pause_workflow()

    def resume_workflow(self) -> WorkflowSnapshot:
        return self.control_plane.resume_workflow()

    async def restart_gatekeeper(self, reason: str | None = None) -> GatekeeperLoopState:
        return await self.control_plane.restart_gatekeeper(reason)

    async def stop_gatekeeper(self) -> GatekeeperLoopState:
        return await self.control_plane.stop_gatekeeper()

    async def run_until_blocked(self) -> list[TaskResult]:
        return await self.control_plane.run_until_blocked()

    async def run_next_task(self) -> TaskResult | None:
        return await self.control_plane.run_next_task()

    def snapshot(self) -> WorkflowSnapshot:
        return self.control_plane.workflow_snapshot()

    def list_recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.event_log.list_recent_events(limit=limit)

    async def shutdown(self) -> None:
        await self.mcp_host.stop()

    def get_consensus_document(self) -> ConsensusDocument | None:
        return self.control_plane.get_consensus_document()

    def get_roadmap(self) -> RoadmapDocument:
        return self.control_plane.get_roadmap()

    def get_task(self, task_id: str) -> TaskInfo | None:
        return self.control_plane.get_task(task_id)

    def get_workflow_status(self):
        return self.control_plane.get_workflow_status()

    def list_roles(self):
        return self.control_plane.list_roles()

    def get_role(self, role: str):
        return self.control_plane.get_role(role)

    def list_instances(self):
        return self.control_plane.list_instances()

    def get_instance(self, agent_id: str):
        return self.control_plane.get_instance(agent_id)

    def list_runs(self):
        return self.control_plane.list_runs()

    def list_active_runs(self):
        return self.control_plane.list_active_runs()

    def get_run(self, run_id: str):
        return self.control_plane.get_run(run_id)

    def list_active_attempts(self):
        return self.control_plane.list_active_attempts()

    def get_review_ticket(self, ticket_id: str):
        return self.control_plane.get_review_ticket(ticket_id)

    def list_pending_review_tickets(self):
        return self.control_plane.list_pending_review_tickets()

    def request_user_decision(self, text: str, **kwargs: Any):
        return self.control_plane.request_user_decision(text, **kwargs)

    def withdraw_question(self, question_id: str, *, reason: str | None = None):
        return self.control_plane.withdraw_question(question_id, reason=reason)

    def update_consensus(self, *, context: str | None = None, status: str | None = None):
        return self.control_plane.update_consensus(context=context, status=status)

    def append_decision(self, **kwargs: Any):
        return self.control_plane.append_decision(**kwargs)

    def add_task(self, task: TaskInfo, *, index: int | None = None):
        return self.control_plane.add_task(task, index=index)

    def update_task_definition(self, task_id: str, **patch: Any):
        return self.control_plane.update_task_definition(task_id, **patch)

    def reorder_tasks(self, task_ids: list[str]):
        return self.control_plane.reorder_tasks(task_ids)

    def replace_roadmap(self, *, tasks: list[TaskInfo], project: str | None = None):
        return self.control_plane.replace_roadmap(tasks=tasks, project=project)

    def end_planning_phase(self):
        return self.control_plane.end_planning_phase()

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


def _repair_runtime_state(
    *,
    workflow_state_store: WorkflowStateStore,
    agent_run_store: AgentRunStore,
    agent_instance_store: AgentInstanceStore,
    runtime_service: AgentRuntimeService,
) -> None:
    live_run_ids = runtime_service.live_run_ids()
    run_by_id = {record.identity.run_id: record for record in agent_run_store.list()}
    _repair_gatekeeper_session_state(
        workflow_state_store=workflow_state_store,
        agent_run_store=agent_run_store,
        live_run_ids=live_run_ids,
    )
    agent_instance_store.reconcile_active_runs(
        live_run_ids=live_run_ids,
        run_by_id=run_by_id,
    )


def _repair_gatekeeper_session_state(
    *,
    workflow_state_store: WorkflowStateStore,
    agent_run_store: AgentRunStore,
    live_run_ids: set[str],
) -> GatekeeperSessionSnapshot:
    state = workflow_state_store.load()
    run_record = (
        agent_run_store.get(state.gatekeeper_session.run_id)
        if state.gatekeeper_session.run_id is not None
        else None
    )
    repaired_session = project_gatekeeper_session(
        state.gatekeeper_session,
        run_record=run_record,
    )
    if (
        repaired_session.run_id is not None
        and repaired_session.run_id not in live_run_ids
        and repaired_session.lifecycle_state in {
            GatekeeperLifecycleStatus.STARTING,
            GatekeeperLifecycleStatus.RUNNING,
        }
    ):
        repaired_session.lifecycle_state = GatekeeperLifecycleStatus.IDLE
        repaired_session.active_turn_id = None
    if asdict(repaired_session) != asdict(state.gatekeeper_session):
        state.gatekeeper_session = repaired_session
        workflow_state_store.save(state)
    return repaired_session
