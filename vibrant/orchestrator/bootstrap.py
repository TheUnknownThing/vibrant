"""Orchestrator bootstrap and composition."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vibrant.agents.code_agent import CodeAgent
from vibrant.agents.gatekeeper import Gatekeeper, GatekeeperAgent
from vibrant.agents.utils import maybe_forward_event
from vibrant.agents.merge_agent import MergeAgent
from vibrant.agents.runtime import AgentRuntime, BaseAgentRuntime
from vibrant.config import DEFAULT_CONFIG_DIR, RoadmapExecutionMode, VibrantConfig, find_project_root, load_config
from vibrant.consensus import RoadmapDocument, RoadmapParser
from vibrant.models.agent import AgentRecord, AgentType
from vibrant.orchestrator.state.backend import OrchestratorStateBackend
from vibrant.project_init import ensure_project_files
from vibrant.providers.base import CanonicalEvent
from vibrant.providers.codex.adapter import CodexProviderAdapter

from .agent_output import AgentOutputProjectionService
from .agents.manager import AgentManagementService
from .agents.registry import AgentRegistry
from .agents.runtime import AgentRuntimeService
from .agents.store import AgentRecordStore
from .artifacts.consensus import ConsensusService
from .artifacts.planning import PlanningService
from .artifacts.questions import QuestionService
from .artifacts.roadmap import RoadmapService
from .artifacts.workflow import WorkflowService
from .execution.git_workspace import GitWorkspaceService, scoped_worktree_root
from .execution.prompts import PromptService
from .execution.retry_policy import RetryPolicyService
from .execution.review import ReviewService
from .execution.service import TaskExecutionService
from .gatekeeper_runtime import GatekeeperRuntimeService
from .git_manager import GitManager
from .state.store import StateStore
from .task_dispatch import TaskDispatcher
from .types import TaskResult

CanonicalEventCallback = Callable[[CanonicalEvent], Any]


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
    _config_holder: dict[str, VibrantConfig] = field(repr=False)

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
        agent_output_service = AgentOutputProjectionService()

        async def handle_canonical_event(event: CanonicalEvent) -> None:
            agent_output_service.ingest(event)
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

        state_store = StateStore(backend)
        agent_store = AgentRecordStore(
            vibrant_dir=vibrant_dir,
            state_store=state_store,
        )
        state_store.bind_agent_store(agent_store)
        roadmap_service = RoadmapService(roadmap_path, project_name=root.name)
        consensus_service = ConsensusService(consensus_path, state_store=state_store)
        agent_registry = AgentRegistry(agent_store=agent_store, vibrant_dir=vibrant_dir)
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
                adapter_factory=resolved_adapter_factory,
                on_canonical_event=handle_canonical_event,
                agent_registry=agent_registry,
            )

        runtime_service = AgentRuntimeService(
            agent_registry=agent_registry,
            adapter_factory=resolved_adapter_factory,
            config_getter=lambda: config_holder["value"],
            on_canonical_event=handle_canonical_event,
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
            adapter_factory=resolved_adapter_factory,
            on_canonical_event=on_canonical_event,
            agent_output_service=agent_output_service,
            state_store=state_store,
            agent_store=agent_store,
            roadmap_service=roadmap_service,
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
        )
        orchestrator.reload_from_disk()
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

    def reload_from_disk(self) -> RoadmapDocument:
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

    async def execute_until_blocked(self) -> list[TaskResult]:
        self.reload_from_disk()
        return await self.execution_service.execute_until_blocked()

    async def execute_next_task(self) -> TaskResult | None:
        self.reload_from_disk()
        return await self.execution_service.execute_next_task()


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
    adapter_factory: Any,
    on_canonical_event: CanonicalEventCallback | None,
    agent_registry: AgentRegistry,
):
    def _build(agent_record: AgentRecord) -> AgentRuntime:
        config = config_getter()
        if agent_record.type is AgentType.GATEKEEPER and isinstance(gatekeeper, Gatekeeper):
            gatekeeper_agent = gatekeeper.agent
            agent = GatekeeperAgent(
                project_root,
                gatekeeper_agent.config,
                adapter_factory=gatekeeper_agent.adapter_factory,
                on_canonical_event=on_canonical_event,
                on_agent_record_updated=agent_registry.make_record_callback(),
                timeout_seconds=gatekeeper_agent.timeout_seconds,
            )
        elif agent_record.type is AgentType.MERGE:
            agent = MergeAgent(
                project_root,
                config,
                adapter_factory=adapter_factory,
                on_canonical_event=on_canonical_event,
                on_agent_record_updated=agent_registry.make_record_callback(),
            )
        else:
            agent = CodeAgent(
                project_root,
                config,
                adapter_factory=adapter_factory,
                on_canonical_event=on_canonical_event,
                on_agent_record_updated=agent_registry.make_record_callback(),
            )
        return BaseAgentRuntime(agent)

    return _build


__all__ = ["Orchestrator", "create_orchestrator"]
