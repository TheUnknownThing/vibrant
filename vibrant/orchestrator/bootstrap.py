"""Orchestrator bootstrap and composition."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vibrant.agents.gatekeeper import Gatekeeper
from vibrant.agents.utils import maybe_forward_event
from vibrant.agents.runtime import AgentRuntime
from vibrant.config import DEFAULT_CONFIG_DIR, RoadmapExecutionMode, VibrantConfig, find_project_root, load_config
from vibrant.consensus import RoadmapDocument, RoadmapParser
from vibrant.models.agent import AgentRunRecord
from vibrant.orchestrator.state.backend import OrchestratorStateBackend
from vibrant.project_init import ensure_project_files
from vibrant.providers.base import CanonicalEvent
from vibrant.providers.codex.adapter import CodexProviderAdapter

from .agents.catalog import (
    AgentRoleCatalog,
    ProviderKindCatalog,
    RoleRuntimeContext,
    build_builtin_provider_catalog,
    build_builtin_role_catalog,
)
from .agents.manager import AgentManagementService
from .agents.output_projection import AgentOutputProjectionService
from .agents.registry import AgentRegistry
from .agents.runtime import AgentRuntimeService
from .agents.store import AgentInstanceStore, AgentRecordStore
from .artifacts.consensus import ConsensusService
from .artifacts.planning import PlanningService
from .artifacts.questions import QuestionService
from .artifacts.roadmap import RoadmapService
from .artifacts.workflow import WorkflowService
from .execution.git_manager import GitManager
from .execution.git_workspace import GitWorkspaceService, scoped_worktree_root
from .execution.prompts import PromptService
from .gatekeeper_runtime import GatekeeperRuntimeService
from .state.store import StateStore
from .tasks.dispatcher import TaskDispatcher
from .tasks.execution import TaskExecutionService
from .tasks.retry import RetryPolicyService
from .tasks.review import ReviewService
from .tasks.store import TaskStore
from .tasks.workflow import TaskWorkflowService
from .types import TaskResult

CanonicalEventCallback = Callable[[CanonicalEvent], Any]


@dataclass(slots=True)
class _RawEventSubscription:
    handler: Any
    agent_id: str | None = None
    task_id: str | None = None
    event_types: frozenset[str] | None = None


@dataclass(slots=True)
class Orchestrator:
    """Concrete orchestrator root object with explicit service dependencies."""

    project_root: Path
    vibrant_dir: Path
    roadmap_path: Path
    consensus_path: Path
    skills_dir: Path
    config: VibrantConfig
    state_backend: OrchestratorStateBackend
    gatekeeper: Gatekeeper | Any
    git_manager: GitManager
    adapter_factory: Any
    on_canonical_event: Any | None
    agent_output_service: AgentOutputProjectionService
    state_store: StateStore
    agent_store: AgentRecordStore
    roadmap_service: RoadmapService
    consensus_service: ConsensusService
    agent_registry: AgentRegistry
    question_service: QuestionService
    git_service: GitWorkspaceService
    prompt_service: PromptService
    workflow_service: WorkflowService
    gatekeeper_runtime: GatekeeperRuntimeService
    review_service: ReviewService
    planning_service: PlanningService
    runtime_service: AgentRuntimeService
    retry_service: RetryPolicyService
    execution_service: TaskExecutionService
    agent_manager: AgentManagementService
    role_catalog: AgentRoleCatalog = field(default_factory=build_builtin_role_catalog)
    provider_catalog: ProviderKindCatalog = field(default_factory=build_builtin_provider_catalog)
    _config_holder: dict[str, VibrantConfig] = field(default_factory=dict, repr=False)
    _raw_event_subscribers: list[_RawEventSubscription] = field(default_factory=list, repr=False)
    task_store: TaskStore | None = None
    task_workflow: TaskWorkflowService | None = None

    @classmethod
    def load(
        cls,
        project_root: str | Path,
        *,
        state_backend: OrchestratorStateBackend | None = None,
        gatekeeper: Gatekeeper | Any | None = None,
        git_manager: GitManager | None = None,
        adapter_factory: Any | None = None,
        on_canonical_event: Any | None = None,
        agent_runtime: AgentRuntime | Any | None = None,
    ) -> Orchestrator:
        root = find_project_root(project_root)
        vibrant_dir = root / DEFAULT_CONFIG_DIR
        roadmap_path = vibrant_dir / "roadmap.md"
        consensus_path = vibrant_dir / "consensus.md"
        skills_dir = vibrant_dir / "skills"

        ensure_project_files(root)
        config = load_config(start_path=root)
        config_holder = {"value": config}
        backend = state_backend or OrchestratorStateBackend.load(root)
        raw_event_subscribers: list[_RawEventSubscription] = []
        agent_output_service = AgentOutputProjectionService()

        async def handle_canonical_event(event: CanonicalEvent) -> None:
            agent_output_service.ingest(event)
            await _dispatch_raw_event_subscribers(raw_event_subscribers, event)
            await maybe_forward_event(on_canonical_event, event)

        resolved_gatekeeper = gatekeeper or Gatekeeper(
            root,
            on_canonical_event=on_canonical_event,
        )
        gatekeeper_agent = getattr(resolved_gatekeeper, "agent", None)
        if gatekeeper_agent is not None:
            gatekeeper_agent.on_canonical_event = handle_canonical_event
        resolved_git_manager = git_manager or GitManager(
            repo_root=root,
            worktree_root=scoped_worktree_root(root, config.worktree_directory),
        )
        resolved_adapter_factory = adapter_factory or CodexProviderAdapter
        role_catalog = build_builtin_role_catalog()
        provider_catalog = build_builtin_provider_catalog(codex_adapter_factory=resolved_adapter_factory)

        state_store = StateStore(backend)
        instance_store = AgentInstanceStore(
            vibrant_dir=vibrant_dir,
        )
        agent_store = AgentRecordStore(
            vibrant_dir=vibrant_dir,
            state_store=state_store,
        )
        state_store.bind_agent_store(agent_store)
        roadmap_service = RoadmapService(roadmap_path, project_name=root.name)
        task_store = TaskStore(state_store=state_store)
        task_workflow = TaskWorkflowService(task_store=task_store)
        roadmap_service.bind_task_state(task_store=task_store, task_workflow=task_workflow)
        consensus_service = ConsensusService(consensus_path, state_store=state_store)
        agent_registry = AgentRegistry(agent_store=agent_store, instance_store=instance_store, vibrant_dir=vibrant_dir)
        agent_registry.role_catalog = role_catalog
        agent_registry.provider_catalog = provider_catalog
        question_service = QuestionService(
            state_store=state_store,
            gatekeeper=resolved_gatekeeper,
        )
        git_service = GitWorkspaceService(project_root=root, git_manager=resolved_git_manager)
        prompt_service = PromptService(
            skills_dir=skills_dir,
            roadmap_parser=roadmap_service.parser,
            consensus_service=consensus_service,
        )
        workflow_service = WorkflowService(
            state_store=state_store,
            roadmap_service=roadmap_service,
            consensus_service=consensus_service,
        )

        runtime_factory = agent_runtime
        if runtime_factory is None:
            runtime_factory = _build_default_agent_runtime_factory(
                project_root=root,
                config_getter=lambda: config_holder["value"],
                gatekeeper=resolved_gatekeeper,
                role_catalog=role_catalog,
                provider_catalog=provider_catalog,
                on_canonical_event=handle_canonical_event,
                agent_registry=agent_registry,
            )

        runtime_service = AgentRuntimeService(
            agent_registry=agent_registry,
            agent_runtime=runtime_factory,
        )
        gatekeeper_runtime = GatekeeperRuntimeService(
            project_root=root,
            state_store=state_store,
            roadmap_service=roadmap_service,
            workflow_service=workflow_service,
            gatekeeper=resolved_gatekeeper,
            agent_registry=agent_registry,
            runtime_service=runtime_service,
        )
        question_service.answer_runner = gatekeeper_runtime.answer_question
        review_service = ReviewService(
            gatekeeper=resolved_gatekeeper,
            state_store=state_store,
            roadmap_service=roadmap_service,
            git_service=git_service,
            task_store=task_store,
            gatekeeper_runner=gatekeeper_runtime.run_request,
        )
        planning_service = PlanningService(
            state_store=state_store,
            question_service=question_service,
            gatekeeper_runtime=gatekeeper_runtime,
            roadmap_service=roadmap_service,
            workflow_service=workflow_service,
        )
        retry_service = RetryPolicyService(
            roadmap_service=roadmap_service,
            review_service=review_service,
            git_service=git_service,
        )
        execution_service = TaskExecutionService(
            state_store=state_store,
            roadmap_service=roadmap_service,
            workflow_service=workflow_service,
            git_service=git_service,
            prompt_service=prompt_service,
            agent_registry=agent_registry,
            runtime_service=runtime_service,
            review_service=review_service,
            retry_service=retry_service,
            task_store=task_store,
        )
        agent_manager = AgentManagementService(
            agent_registry=agent_registry,
            runtime_service=runtime_service,
            execution_service=execution_service,
            output_service=agent_output_service,
        )

        orchestrator = cls(
            project_root=root,
            vibrant_dir=vibrant_dir,
            roadmap_path=roadmap_path,
            consensus_path=consensus_path,
            skills_dir=skills_dir,
            config=config,
            state_backend=backend,
            gatekeeper=resolved_gatekeeper,
            git_manager=resolved_git_manager,
            role_catalog=role_catalog,
            provider_catalog=provider_catalog,
            adapter_factory=resolved_adapter_factory,
            on_canonical_event=on_canonical_event,
            agent_output_service=agent_output_service,
            state_store=state_store,
            agent_store=agent_store,
            roadmap_service=roadmap_service,
            task_store=task_store,
            task_workflow=task_workflow,
            consensus_service=consensus_service,
            agent_registry=agent_registry,
            question_service=question_service,
            git_service=git_service,
            prompt_service=prompt_service,
            workflow_service=workflow_service,
            gatekeeper_runtime=gatekeeper_runtime,
            review_service=review_service,
            planning_service=planning_service,
            runtime_service=runtime_service,
            retry_service=retry_service,
            execution_service=execution_service,
            agent_manager=agent_manager,
            _config_holder=config_holder,
            _raw_event_subscribers=raw_event_subscribers,
        )
        orchestrator.refresh()
        return orchestrator

    @property
    def roadmap_parser(self) -> RoadmapParser:
        return self.roadmap_service.parser

    @property
    def roadmap_document(self) -> RoadmapDocument | None:
        return self.roadmap_service.document

    @property
    def dispatcher(self) -> TaskDispatcher | None:
        return self.roadmap_service.dispatcher

    @property
    def execution_mode(self) -> RoadmapExecutionMode:
        return self.config.execution_mode

    @property
    def gatekeeper_busy(self) -> bool:
        return self.gatekeeper_runtime.busy

    def refresh(self) -> RoadmapDocument:
        self.config = load_config(start_path=self.project_root)
        self._config_holder["value"] = self.config
        self.state_store.refresh()
        return self.roadmap_service.reload(
            project_name=self.project_root.name,
            concurrency_limit=self.state_store.state.concurrency_limit,
        )

    async def start_gatekeeper_message(self, text: str) -> Any:
        return await self.planning_service.start_message(text)

    async def submit_gatekeeper_message(self, text: str) -> Any:
        return await self.planning_service.submit_message(text)

    async def answer_pending_question(self, answer: str, *, question: str | None = None) -> Any:
        return await self.question_service.answer(answer, question=question)

    async def run_until_blocked(self) -> list[TaskResult]:
        self.refresh()
        return await self.agent_manager.execute_until_blocked()

    async def run_next_task(self) -> TaskResult | None:
        self.refresh()
        return await self.agent_manager.execute_next_task()

    async def _publish_raw_event(self, event: CanonicalEvent) -> None:
        await _dispatch_raw_event_subscribers(self._raw_event_subscribers, event)

    def subscribe_raw_events(
        self,
        handler: Any,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        event_types: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> Callable[[], None]:
        normalized_event_types = None
        if event_types is not None:
            normalized_event_types = frozenset(
                event_type.strip()
                for event_type in event_types
                if isinstance(event_type, str) and event_type.strip()
            )
        subscription = _RawEventSubscription(
            handler=handler,
            agent_id=agent_id,
            task_id=task_id,
            event_types=normalized_event_types,
        )
        self._raw_event_subscribers.append(subscription)

        def unsubscribe() -> None:
            try:
                self._raw_event_subscribers.remove(subscription)
            except ValueError:
                return

        return unsubscribe


def create_orchestrator(
    project_root: str | Path,
    *,
    state_backend: OrchestratorStateBackend | None = None,
    gatekeeper: Gatekeeper | Any | None = None,
    git_manager: GitManager | None = None,
    adapter_factory: Any | None = None,
    on_canonical_event: Any | None = None,
    agent_runtime: AgentRuntime | Any | None = None,
) -> Orchestrator:
    """Build a fully wired orchestrator for one project."""

    return Orchestrator.load(
        project_root,
        state_backend=state_backend,
        gatekeeper=gatekeeper,
        git_manager=git_manager,
        adapter_factory=adapter_factory,
        on_canonical_event=on_canonical_event,
        agent_runtime=agent_runtime,
    )


def _build_default_agent_runtime_factory(
    *,
    project_root: Path,
    config_getter: Callable[[], VibrantConfig],
    gatekeeper: Gatekeeper | Any,
    role_catalog: AgentRoleCatalog,
    provider_catalog: ProviderKindCatalog,
    on_canonical_event: CanonicalEventCallback | None,
    agent_registry: AgentRegistry,
):
    def _build(agent_record: AgentRunRecord) -> AgentRuntime:
        config = config_getter()
        role_spec = role_catalog.get(agent_record.identity.role)
        return role_spec.build_runtime(
            RoleRuntimeContext(
                project_root=project_root,
                agent_record=agent_record,
                config=config,
                gatekeeper=gatekeeper,
                provider_catalog=provider_catalog,
                on_canonical_event=on_canonical_event,
                on_agent_record_updated=agent_registry.make_record_callback(),
            )
        )

    return _build


async def _dispatch_raw_event_subscribers(
    subscribers: list[_RawEventSubscription],
    event: CanonicalEvent,
) -> None:
    for subscription in tuple(subscribers):
        if not _raw_event_matches(subscription, event):
            continue
        await maybe_forward_event(subscription.handler, event)


def _raw_event_matches(subscription: _RawEventSubscription, event: CanonicalEvent) -> bool:
    if subscription.agent_id is not None and event.get("agent_id") != subscription.agent_id:
        return False
    if subscription.task_id is not None and event.get("task_id") != subscription.task_id:
        return False
    if subscription.event_types is not None and str(event.get("type") or "") not in subscription.event_types:
        return False
    return True


__all__ = ["Orchestrator", "create_orchestrator"]
