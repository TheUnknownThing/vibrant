"""Thin compatibility wrapper around orchestrator services."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from vibrant.config import DEFAULT_CONFIG_DIR, RoadmapExecutionMode, find_project_root, load_config
from vibrant.consensus import RoadmapDocument, RoadmapParser
from vibrant.gatekeeper import Gatekeeper, GatekeeperRequest, GatekeeperRunResult, GatekeeperTrigger
from vibrant.models.state import OrchestratorStatus
from vibrant.orchestrator.engine import OrchestratorEngine
from vibrant.orchestrator.git_manager import GitManager
from vibrant.project_init import ensure_project_files
from vibrant.providers.base import CanonicalEvent
from vibrant.providers.codex.adapter import CodexProviderAdapter

from .services import (
    AgentRegistry,
    AgentRuntimeService,
    ConsensusService,
    GitWorkspaceService,
    PlanningService,
    PromptService,
    QuestionService,
    RetryPolicyService,
    ReviewService,
    RoadmapService,
    StateStore,
    TaskExecutionService,
    WorkflowService,
)
from .services.git_workspace import scoped_worktree_root
from .task_dispatch import TaskDispatcher
from .types import CodeAgentLifecycleResult

CanonicalEventCallback = Callable[[CanonicalEvent], Any]


@dataclass(slots=True)
class _AsyncGatekeeperHandle:
    """Minimal compatibility handle for in-flight Gatekeeper operations."""

    result_future: asyncio.Future[GatekeeperRunResult]

    def done(self) -> bool:
        return self.result_future.done()

    async def wait(self) -> GatekeeperRunResult:
        return await self.result_future


class CodeAgentLifecycle:
    """Compatibility wrapper that delegates orchestration to services."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        engine: OrchestratorEngine | None = None,
        gatekeeper: Gatekeeper | Any | None = None,
        git_manager: GitManager | None = None,
        adapter_factory: Any | None = None,
        on_canonical_event: CanonicalEventCallback | None = None,
    ) -> None:
        self.project_root = find_project_root(project_root)
        self.vibrant_dir = self.project_root / DEFAULT_CONFIG_DIR
        self.roadmap_path = self.vibrant_dir / "roadmap.md"
        self.consensus_path = self.vibrant_dir / "consensus.md"
        self.skills_dir = self.vibrant_dir / "skills"

        ensure_project_files(self.project_root)
        self.config = load_config(start_path=self.project_root)
        self.engine = engine or OrchestratorEngine.load(self.project_root)
        self.gatekeeper = gatekeeper or Gatekeeper(
            self.project_root,
            on_canonical_event=on_canonical_event,
        )
        self.git_manager = git_manager or GitManager(
            repo_root=self.project_root,
            worktree_root=scoped_worktree_root(self.project_root, self.config.worktree_directory),
        )
        self.adapter_factory = adapter_factory or CodexProviderAdapter
        self.on_canonical_event = on_canonical_event

        self.state_store = StateStore(self.engine)
        self.roadmap_service = RoadmapService(self.roadmap_path)
        self.consensus_service = ConsensusService(self.consensus_path, state_store=self.state_store)
        self.agent_registry = AgentRegistry(engine=self.engine, vibrant_dir=self.vibrant_dir)
        self.question_service = QuestionService(state_store=self.state_store, gatekeeper=self.gatekeeper)
        self.git_service = GitWorkspaceService(project_root=self.project_root, git_manager=self.git_manager)
        self.prompt_service = PromptService(
            skills_dir=self.skills_dir,
            roadmap_parser=self.roadmap_service.parser,
            consensus_service=self.consensus_service,
        )
        self.workflow_service = WorkflowService(
            state_store=self.state_store,
            roadmap_service=self.roadmap_service,
            consensus_service=self.consensus_service,
        )
        self.review_service = ReviewService(
            gatekeeper=self.gatekeeper,
            state_store=self.state_store,
            roadmap_service=self.roadmap_service,
            git_service=self.git_service,
        )
        self.planning_service = PlanningService(
            state_store=self.state_store,
            question_service=self.question_service,
            review_service=self.review_service,
            roadmap_service=self.roadmap_service,
            workflow_service=self.workflow_service,
        )
        self.runtime_service = AgentRuntimeService(
            agent_registry=self.agent_registry,
            adapter_factory=self.adapter_factory,
            config_getter=lambda: self.config,
            on_canonical_event=self.on_canonical_event,
        )
        self.retry_service = RetryPolicyService(
            roadmap_service=self.roadmap_service,
            review_service=self.review_service,
            git_service=self.git_service,
        )
        self.execution_service = TaskExecutionService(
            state_store=self.state_store,
            roadmap_service=self.roadmap_service,
            workflow_service=self.workflow_service,
            git_service=self.git_service,
            prompt_service=self.prompt_service,
            agent_registry=self.agent_registry,
            runtime_service=self.runtime_service,
            review_service=self.review_service,
            retry_service=self.retry_service,
        )
        self._active_gatekeeper_futures: set[asyncio.Future[Any]] = set()

        self.reload_from_disk()

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
        return any(not future.done() for future in self._active_gatekeeper_futures)

    def reload_from_disk(self) -> RoadmapDocument:
        self.config = load_config(start_path=self.project_root)
        self.state_store.refresh()
        return self.roadmap_service.reload(
            project_name=self.project_root.name,
            concurrency_limit=self.engine.state.concurrency_limit,
        )

    async def start_gatekeeper_message(self, text: str) -> Any:
        self.reload_from_disk()
        message = text.strip()
        if not message:
            raise ValueError("Gatekeeper message cannot be empty")
        if self.gatekeeper_busy:
            raise RuntimeError("Gatekeeper is already running")

        pending_question = self.question_service.current_question()
        if pending_question is not None:
            result_future = asyncio.create_task(
                self.question_service.answer(message, question=pending_question),
                name="gatekeeper-answer-question",
            )
            self._track_gatekeeper_future(result_future)
            return _AsyncGatekeeperHandle(result_future)

        trigger = (
            GatekeeperTrigger.PROJECT_START
            if self.state_store.state.status is OrchestratorStatus.INIT
            else GatekeeperTrigger.USER_CONVERSATION
        )
        request = GatekeeperRequest(
            trigger=trigger,
            trigger_description=message,
            agent_summary=message,
        )
        resume_latest_thread = trigger is GatekeeperTrigger.USER_CONVERSATION

        start_run = getattr(self.gatekeeper, "start_run", None)
        if callable(start_run):
            kwargs: dict[str, Any] = {}
            supports_on_result = False
            try:
                signature = inspect.signature(start_run)
            except (TypeError, ValueError):
                signature = None

            if signature is not None:
                if "resume_latest_thread" in signature.parameters:
                    kwargs["resume_latest_thread"] = resume_latest_thread
                if "on_result" in signature.parameters:
                    kwargs["on_result"] = self._apply_gatekeeper_result_async
                    supports_on_result = True

            handle = await start_run(request, **kwargs)
            if supports_on_result:
                self._track_gatekeeper_future(handle.result_future)
                return handle

            async def wait_and_apply() -> GatekeeperRunResult:
                result = await handle.wait()
                await self._apply_gatekeeper_result_async(result)
                return result

            result_future = asyncio.create_task(
                wait_and_apply(),
                name=f"gatekeeper-{trigger.value}",
            )
            self._track_gatekeeper_future(result_future)
            return _AsyncGatekeeperHandle(result_future)

        result_future = asyncio.create_task(
            self.planning_service.submit_message(message),
            name=f"gatekeeper-{trigger.value}",
        )
        self._track_gatekeeper_future(result_future)
        return _AsyncGatekeeperHandle(result_future)

    async def submit_gatekeeper_message(self, text: str) -> GatekeeperRunResult:
        handle = await self.start_gatekeeper_message(text)
        return await handle.wait()

    async def _apply_gatekeeper_result_async(self, result: GatekeeperRunResult) -> None:
        self.state_store.apply_gatekeeper_result(result)
        self.roadmap_service.merge_result(result.roadmap_document)
        self.roadmap_service.persist()
        self.workflow_service.maybe_complete_workflow()
        self.state_store.refresh()

    def _track_gatekeeper_future(self, future: asyncio.Future[Any]) -> None:
        self._active_gatekeeper_futures.add(future)

        def discard(done_future: asyncio.Future[Any]) -> None:
            self._active_gatekeeper_futures.discard(done_future)

        future.add_done_callback(discard)

    async def execute_until_blocked(self) -> list[CodeAgentLifecycleResult]:
        self.reload_from_disk()
        return await self.execution_service.execute_until_blocked()

    async def execute_next_task(self) -> CodeAgentLifecycleResult | None:
        self.reload_from_disk()
        return await self.execution_service.execute_next_task()


__all__ = ["CodeAgentLifecycle", "CodeAgentLifecycleResult"]
