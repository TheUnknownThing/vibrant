"""Bootstrap and composition root for the layered orchestrator."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from vibrant.agents.gatekeeper import Gatekeeper
from vibrant.config import DEFAULT_CONFIG_DIR, RoadmapExecutionMode, VibrantConfig, find_project_root, load_config
from vibrant.consensus.roadmap import RoadmapDocument
from vibrant.project_init import ensure_project_files
from vibrant.providers.registry import resolve_configured_adapter_factory

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
    WorkspaceStore,
)
from .basic.stores.gatekeeper_session import project_gatekeeper_session
from .basic.workspace import WorkspaceService
from .interface import BasicQueryAdapter, InterfaceControlPlane, OrchestratorBackend, PolicyCommandAdapter
from .interface.mcp import OrchestratorFastMCPHost, OrchestratorMCPServer
from .policy.gatekeeper_loop import GatekeeperLifecycleService, GatekeeperUserLoop
from .policy.task_loop import ExecutionCoordinator, TaskLoop
from .types import CanonicalEventHandler, GatekeeperLifecycleStatus, GatekeeperSessionSnapshot, ProviderAdapterFactory


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
    workspace_store: WorkspaceStore
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
    adapter_factory: ProviderAdapterFactory

    @classmethod
    def load(
        cls,
        project_root: str | Path,
        *,
        gatekeeper: Gatekeeper | None = None,
        adapter_factory: ProviderAdapterFactory | None = None,
        on_canonical_event: CanonicalEventHandler | None = None,
        **_: Any,
    ) -> "Orchestrator":
        root = find_project_root(project_root)
        ensure_project_files(root)
        vibrant_dir = root / DEFAULT_CONFIG_DIR
        config = load_config(start_path=root)
        resolved_adapter_factory = resolve_configured_adapter_factory(config, adapter_factory=adapter_factory)

        workflow_state_store = WorkflowStateStore(vibrant_dir / "state.json")
        attempt_store = AttemptStore(vibrant_dir / "attempts.json")
        question_store = QuestionStore(vibrant_dir / "questions.json")
        consensus_store = ConsensusStore(vibrant_dir / "consensus.md", project_name=root.name)
        roadmap_store = RoadmapStore(vibrant_dir / "roadmap.md", project_name=root.name)
        review_ticket_store = ReviewTicketStore(vibrant_dir / "reviews.json")
        workspace_store = WorkspaceStore(vibrant_dir / "workspaces.json")
        agent_instance_store = AgentInstanceStore(vibrant_dir / "agent-instances")
        agent_run_store = AgentRunStore(vibrant_dir / "agent-runs")
        conversation_store = ConversationStore(vibrant_dir)
        conversation_stream = ConversationStreamService(conversation_store)
        runtime_service = AgentRuntimeService()
        workspace_service = WorkspaceService(
            project_root=root,
            worktree_root=config.worktree_directory,
            workspace_store=workspace_store,
            artifacts_root=vibrant_dir / "review-diffs",
        )
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
        execution_coordinator.reconcile_active_sessions()
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
            conversation_store=conversation_store,
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
        gatekeeper_lifecycle.attach_mcp_bridge(
            binding_service=session_binding,
            mcp_host=mcp_host,
        )
        execution_coordinator.attach_mcp_bridge(
            binding_service=session_binding,
            mcp_host=mcp_host,
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
            workspace_store=workspace_store,
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

    async def shutdown(self) -> None:
        await self.mcp_host.stop()


def create_orchestrator(
    project_root: str | Path,
    *,
    gatekeeper: Gatekeeper | None = None,
    adapter_factory: ProviderAdapterFactory | None = None,
    on_canonical_event: CanonicalEventHandler | None = None,
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
