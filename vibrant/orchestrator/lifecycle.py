"""Thin compatibility wrapper around orchestrator services."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vibrant.agents.code_agent import CodeAgent
from vibrant.agents.gatekeeper import Gatekeeper, GatekeeperAgent, GatekeeperRequest, GatekeeperRunResult, GatekeeperTrigger
from vibrant.agents.merge_agent import MergeAgent
from vibrant.agents.runtime import AgentRuntime, BaseAgentRuntime
from vibrant.config import DEFAULT_CONFIG_DIR, RoadmapExecutionMode, find_project_root, load_config
from vibrant.consensus import RoadmapDocument, RoadmapParser
from vibrant.models.agent import AgentRecord, AgentType
from vibrant.models.state import OrchestratorStatus
from vibrant.orchestrator.execution.git_manager import GitManager
from vibrant.orchestrator.state.backend import OrchestratorStateBackend
from vibrant.project_init import ensure_project_files
from vibrant.providers.base import CanonicalEvent
from vibrant.providers.codex.adapter import CodexProviderAdapter
from vibrant.agents.utils import maybe_forward_event

from .agents import AgentManagementService, AgentRecordStore, AgentRegistry, AgentRuntimeService
from .agent_output import AgentOutputProjectionService
from .execution.dispatcher import TaskDispatcher
from .execution import GitWorkspaceService, PromptService, RetryPolicyService, ReviewService, TaskExecutionService, scoped_worktree_root
from .artifacts import ConsensusService, PlanningService, QuestionService, RoadmapService, WorkflowService
from .state import StateStore
from .types import CodeAgentLifecycleResult

CanonicalEventCallback = Callable[[CanonicalEvent], Any]
GatekeeperResultCallback = Callable[[GatekeeperRunResult], Any | Awaitable[Any]]
_MIN_TIME = datetime.min.replace(tzinfo=timezone.utc)


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
        engine: OrchestratorStateBackend | None = None,
        gatekeeper: Gatekeeper | Any | None = None,
        git_manager: GitManager | None = None,
        adapter_factory: Any | None = None,
        on_canonical_event: CanonicalEventCallback | None = None,
        agent_runtime: AgentRuntime | None = None,
    ) -> None:
        self.project_root = find_project_root(project_root)
        self.vibrant_dir = self.project_root / DEFAULT_CONFIG_DIR
        self.roadmap_path = self.vibrant_dir / "roadmap.md"
        self.consensus_path = self.vibrant_dir / "consensus.md"
        self.skills_dir = self.vibrant_dir / "skills"

        ensure_project_files(self.project_root)
        self.config = load_config(start_path=self.project_root)
        self.engine = engine or OrchestratorStateBackend.load(self.project_root)
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
        self.agent_output_service = AgentOutputProjectionService()

        self.state_store = StateStore(self.engine)
        self.agent_store = AgentRecordStore(
            vibrant_dir=self.vibrant_dir,
            state_store=self.state_store,
        )
        self.state_store.bind_agent_store(self.agent_store)
        self.roadmap_service = RoadmapService(self.roadmap_path, project_name=self.project_root.name)
        self.consensus_service = ConsensusService(self.consensus_path, state_store=self.state_store)
        self.agent_registry = AgentRegistry(agent_store=self.agent_store, vibrant_dir=self.vibrant_dir)
        self.question_service = QuestionService(
            state_store=self.state_store,
            gatekeeper=self.gatekeeper,
            answer_runner=self._answer_gatekeeper_question,
        )
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
            gatekeeper_runner=self._run_gatekeeper_request,
        )
        self.planning_service = PlanningService(
            state_store=self.state_store,
            question_service=self.question_service,
            review_service=self.review_service,
            roadmap_service=self.roadmap_service,
            workflow_service=self.workflow_service,
        )

        # Build the protocol-based agent runtime.
        # Callers can supply a pre-built AgentRuntime (e.g. for testing or
        # remote agents).  Otherwise we construct a BaseAgentRuntime wrapping
        # a CodeAgent so the orchestrator drives execution through the
        # protocol boundary rather than inline adapter logic.
        self._agent_runtime: AgentRuntime | Callable[[AgentRecord], AgentRuntime] | None = agent_runtime
        if self._agent_runtime is None:
            self._agent_runtime = self._build_default_agent_runtime

        self.runtime_service = AgentRuntimeService(
            agent_registry=self.agent_registry,
            adapter_factory=self.adapter_factory,
            config_getter=lambda: self.config,
            on_canonical_event=self._handle_runtime_canonical_event,
            agent_runtime=self._agent_runtime,
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
        self.agent_manager = AgentManagementService(
            agent_registry=self.agent_registry,
            runtime_service=self.runtime_service,
            execution_service=self.execution_service,
            output_service=self.agent_output_service,
        )
        self._active_gatekeeper_futures: set[asyncio.Future[Any]] = set()

        self.reload_from_disk()

    def _build_default_agent_runtime(self, agent_record: AgentRecord) -> AgentRuntime:
        """Create a fresh protocol runtime for one agent record.

        We intentionally build a fresh wrapped agent per run so callback wiring
        stays isolated even if multiple tasks overlap in the future.
        """
        if agent_record.type is AgentType.GATEKEEPER and isinstance(self.gatekeeper, Gatekeeper):
            gatekeeper_agent = self.gatekeeper.agent
            agent = GatekeeperAgent(
                self.project_root,
                gatekeeper_agent.config,
                adapter_factory=gatekeeper_agent.adapter_factory,
                on_canonical_event=self._handle_runtime_canonical_event,
                on_agent_record_updated=self.agent_registry.make_record_callback(),
                timeout_seconds=gatekeeper_agent.timeout_seconds,
            )
        elif agent_record.type is AgentType.MERGE:
            agent = MergeAgent(
                self.project_root,
                self.config,
                adapter_factory=self.adapter_factory,
                on_canonical_event=self._handle_runtime_canonical_event,
                on_agent_record_updated=self.agent_registry.make_record_callback(),
            )
        else:
            agent = CodeAgent(
                self.project_root,
                self.config,
                adapter_factory=self.adapter_factory,
                on_canonical_event=self._handle_runtime_canonical_event,
                on_agent_record_updated=self.agent_registry.make_record_callback(),
            )
        return BaseAgentRuntime(agent)

    async def _handle_runtime_canonical_event(self, event: CanonicalEvent) -> None:
        self.agent_output_service.ingest(event)
        await maybe_forward_event(self.on_canonical_event, event)

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
        managed_busy = any(
            snapshot.active and not snapshot.awaiting_input
            for snapshot in self.agent_manager.list_agents(
                agent_type=AgentType.GATEKEEPER,
                include_completed=False,
            )
        )
        return managed_busy or any(not future.done() for future in self._active_gatekeeper_futures)

    def _uses_managed_gatekeeper_runtime(self) -> bool:
        return isinstance(self.gatekeeper, Gatekeeper) and self.runtime_service.supports_handles

    def _latest_gatekeeper_thread_id(self) -> str | None:
        latest_record: AgentRecord | None = None
        latest_sort_key: tuple[object, object] | None = None
        for record in self.agent_registry.list_records():
            if record.type is not AgentType.GATEKEEPER:
                continue
            thread_id = record.provider.provider_thread_id or _extract_provider_thread_id(record.provider.resume_cursor)
            if not thread_id:
                continue
            started = record.started_at or record.finished_at or _MIN_TIME
            finished = record.finished_at or started
            sort_key = (started, finished)
            if latest_sort_key is None or sort_key > latest_sort_key:
                latest_record = record
                latest_sort_key = sort_key
        if latest_record is None:
            return None
        return latest_record.provider.provider_thread_id or _extract_provider_thread_id(
            latest_record.provider.resume_cursor
        )

    async def _forward_gatekeeper_result(
        self,
        *,
        agent_id: str,
        callback: GatekeeperResultCallback,
    ) -> GatekeeperRunResult:
        execution_result = await self.agent_manager.wait_for_agent(agent_id)
        result = execution_result.normalized_result
        if result is None:
            raise RuntimeError(f"Gatekeeper run {agent_id} did not produce a normalized result")
        callback_result = callback(result)
        if asyncio.iscoroutine(callback_result):
            await callback_result
        return result

    async def _start_managed_gatekeeper_run(
        self,
        request: GatekeeperRequest,
        *,
        resume_latest_thread: bool | None = None,
        on_result: GatekeeperResultCallback | None = None,
    ) -> Any:
        if not isinstance(self.gatekeeper, Gatekeeper):
            raise RuntimeError("Managed Gatekeeper runtime requires a real Gatekeeper instance")

        prompt = self.gatekeeper.render_prompt(request)
        agent_record = self.gatekeeper.build_agent_record(request)
        agent_record.prompt_used = prompt

        should_resume = (
            resume_latest_thread
            if resume_latest_thread is not None
            else request.trigger is GatekeeperTrigger.USER_CONVERSATION
        )
        resume_thread_id = self._latest_gatekeeper_thread_id() if should_resume else None
        handle = await self.agent_manager.start_run(
            agent_record=agent_record,
            prompt=prompt,
            cwd=str(self.project_root),
            resume_thread_id=resume_thread_id,
            increment_spawn=True,
        )
        setattr(handle, "agent_record", agent_record)
        setattr(handle, "request", request)
        setattr(handle, "prompt", prompt)

        if on_result is not None:
            future = asyncio.create_task(
                self._forward_gatekeeper_result(agent_id=agent_record.agent_id, callback=on_result),
                name=f"gatekeeper-{request.trigger.value}-{agent_record.agent_id}",
            )
            self._track_gatekeeper_future(future)

        return handle

    async def _run_gatekeeper_request(
        self,
        request: GatekeeperRequest,
        resume_latest_thread: bool | None = None,
    ) -> GatekeeperRunResult:
        if self._uses_managed_gatekeeper_runtime():
            handle = await self._start_managed_gatekeeper_run(
                request,
                resume_latest_thread=resume_latest_thread,
            )
            execution_result = await self.agent_manager.wait_for_agent(handle.agent_record.agent_id)
            result = execution_result.normalized_result
            if result is None:
                raise RuntimeError(f"Gatekeeper run {handle.agent_record.agent_id} did not produce a normalized result")
            return result

        run_gatekeeper = self.gatekeeper.run
        try:
            from inspect import signature

            parameters = signature(run_gatekeeper).parameters
        except (TypeError, ValueError):
            parameters = {}

        if "resume_latest_thread" in parameters:
            return await run_gatekeeper(request, resume_latest_thread=resume_latest_thread)
        return await run_gatekeeper(request)

    async def _answer_gatekeeper_question(self, question: str, answer: str) -> GatekeeperRunResult:
        request = GatekeeperRequest(
            trigger=GatekeeperTrigger.USER_CONVERSATION,
            trigger_description=f"Question: {question}\nUser Answer: {answer}",
            agent_summary=answer,
        )
        return await self._run_gatekeeper_request(request, resume_latest_thread=True)

    def reload_from_disk(self) -> RoadmapDocument:
        self.config = load_config(start_path=self.project_root)
        self.state_store.refresh()
        self.agent_store.refresh()
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

        if self._uses_managed_gatekeeper_runtime():
            return await self._start_managed_gatekeeper_run(
                request,
                resume_latest_thread=resume_latest_thread,
                on_result=self._apply_gatekeeper_result_async,
            )

        start_run = getattr(self.gatekeeper, "start_run", None)
        if callable(start_run):
            from inspect import signature

            kwargs: dict[str, Any] = {}
            supports_on_result = False
            try:
                parameters = signature(start_run).parameters
            except (TypeError, ValueError):
                parameters = {}

            if "resume_latest_thread" in parameters:
                kwargs["resume_latest_thread"] = resume_latest_thread
            if "on_result" in parameters:
                kwargs["on_result"] = self._apply_gatekeeper_result_async
                supports_on_result = True

            handle = await start_run(request, **kwargs)
            if supports_on_result:
                self._track_gatekeeper_future(asyncio.create_task(handle.wait(), name=f"gatekeeper-wait-{trigger.value}"))
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

    async def answer_pending_question(self, answer: str, *, question: str | None = None) -> GatekeeperRunResult:
        return await self.question_service.answer(answer, question=question)

    async def _apply_gatekeeper_result_async(self, result: GatekeeperRunResult) -> None:
        self.state_store.apply_gatekeeper_result(result)
        self.roadmap_service.reload(
            project_name=self.roadmap_service.project_name,
            concurrency_limit=self.state_store.state.concurrency_limit,
        )
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
        return await self.agent_manager.execute_until_blocked()

    async def execute_next_task(self) -> CodeAgentLifecycleResult | None:
        self.reload_from_disk()
        return await self.agent_manager.execute_next_task()


__all__ = ["CodeAgentLifecycle", "CodeAgentLifecycleResult"]


def _extract_provider_thread_id(resume_cursor: object) -> str | None:
    if not isinstance(resume_cursor, dict):
        return None
    thread_id = resume_cursor.get("threadId")
    return thread_id if isinstance(thread_id, str) and thread_id else None
