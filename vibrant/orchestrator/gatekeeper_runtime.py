"""Gatekeeper runtime orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import inspect
from pathlib import Path
from typing import Any

from vibrant.agents.gatekeeper import Gatekeeper, GatekeeperRequest, GatekeeperRunResult, GatekeeperTrigger
from vibrant.models.agent import AgentRecord, AgentStatus

from .agents.catalog import build_builtin_role_catalog
from .agents.instance import ManagedAgentInstance
from .agents.registry import AgentRegistry
from .agents.runtime import AgentRuntimeService
from .artifacts.roadmap import RoadmapService
from .artifacts.workflow import WorkflowService
from .state.store import StateStore

GatekeeperResultCallback = Callable[[GatekeeperRunResult], Any | Awaitable[Any]]
_MIN_TIME = datetime.min.replace(tzinfo=timezone.utc)
_ROLE_CATALOG = build_builtin_role_catalog()


@dataclass(slots=True)
class AsyncGatekeeperHandle:
    """Minimal handle for in-flight Gatekeeper interactions."""

    result_future: asyncio.Future[GatekeeperRunResult]

    def done(self) -> bool:
        return self.result_future.done()

    async def wait(self) -> GatekeeperRunResult:
        return await self.result_future


class GatekeeperRuntimeService:
    """Own Gatekeeper session/runtime coordination."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        state_store: StateStore,
        roadmap_service: RoadmapService,
        workflow_service: WorkflowService,
        gatekeeper: Gatekeeper | Any,
        agent_registry: AgentRegistry,
        runtime_service: AgentRuntimeService,
    ) -> None:
        self.project_root = Path(project_root)
        self.state_store = state_store
        self.roadmap_service = roadmap_service
        self.workflow_service = workflow_service
        self.gatekeeper = gatekeeper
        self.agent_registry = agent_registry
        self.runtime_service = runtime_service
        self._active_futures: set[asyncio.Future[Any]] = set()

    @property
    def busy(self) -> bool:
        managed_busy = any(
            record.identity.role == "gatekeeper"
            and record.lifecycle.status not in AgentRecord.TERMINAL_STATUSES
            and record.lifecycle.status is not AgentStatus.AWAITING_INPUT
            for record in self.agent_registry.list_records()
        )
        return managed_busy or any(not future.done() for future in self._active_futures)

    def _uses_managed_runtime(self) -> bool:
        return isinstance(self.gatekeeper, Gatekeeper) and self.runtime_service.supports_handles

    def _managed_instance(self, *, role: str, scope_type: str, scope_id: str | None) -> ManagedAgentInstance:
        return ManagedAgentInstance.from_record(
            self.agent_registry.resolve_instance(
                role=role,
                scope_type=scope_type,
                scope_id=scope_id,
            ),
            agent_registry=self.agent_registry,
            runtime_service=self.runtime_service,
        )

    def _gatekeeper_instance(self) -> ManagedAgentInstance:
        return self._managed_instance(role="gatekeeper", scope_type="project", scope_id="project")

    def _latest_thread_id(self) -> str | None:
        provider_thread = self._gatekeeper_instance().provider_thread_handle()
        if provider_thread is None or not provider_thread.resumable:
            return None
        return provider_thread.thread_id

    def track_future(self, future: asyncio.Future[Any]) -> None:
        self._active_futures.add(future)

        def discard(done_future: asyncio.Future[Any]) -> None:
            self._active_futures.discard(done_future)

        future.add_done_callback(discard)

    def future_handle(self, future: asyncio.Future[GatekeeperRunResult]) -> AsyncGatekeeperHandle:
        self.track_future(future)
        return AsyncGatekeeperHandle(future)

    async def _maybe_invoke_callback(
        self,
        callback: GatekeeperResultCallback | None,
        result: GatekeeperRunResult,
    ) -> GatekeeperRunResult:
        if callback is None:
            return result
        callback_result = callback(result)
        if asyncio.iscoroutine(callback_result):
            await callback_result
        return result

    async def _forward_managed_result(
        self,
        *,
        agent: ManagedAgentInstance,
        callback: GatekeeperResultCallback,
    ) -> GatekeeperRunResult:
        execution_result = await agent.wait_for_run()
        result = execution_result.normalized_result
        if result is None:
            raise RuntimeError(f"Gatekeeper run {agent.agent_id} did not produce a normalized result")
        return await self._maybe_invoke_callback(callback, result)

    async def start_request(
        self,
        request: GatekeeperRequest,
        *,
        resume_latest_thread: bool | None = None,
        on_result: GatekeeperResultCallback | None = None,
    ) -> Any:
        if self._uses_managed_runtime():
            prompt = self.gatekeeper.render_prompt(request)
            agent_instance = self._gatekeeper_instance()

            should_resume = (
                resume_latest_thread
                if resume_latest_thread is not None
                else request.trigger is GatekeeperTrigger.USER_CONVERSATION
            )
            resume_thread_id = self._latest_thread_id() if should_resume else None
            started_run = await agent_instance.start_new_run(
                task_id=f"gatekeeper-{request.trigger.value}",
                branch=None,
                worktree_path=str(self.project_root),
                prompt=prompt,
                cwd=str(self.project_root),
                resume_thread_id=resume_thread_id,
                increment_spawn=True,
            )
            started_run.agent_record.context.prompt_used = prompt
            setattr(started_run.handle, "agent_record", started_run.agent_record)
            setattr(started_run.handle, "agent_instance", started_run.agent)
            setattr(started_run.handle, "request", request)
            setattr(started_run.handle, "prompt", prompt)

            if on_result is not None:
                future = asyncio.create_task(
                    self._forward_managed_result(agent=started_run.agent, callback=on_result),
                    name=f"gatekeeper-{request.trigger.value}-{started_run.agent_record.identity.run_id}",
                )
                self.track_future(future)

            return started_run.handle

        start_run = getattr(self.gatekeeper, "start_run", None)
        if callable(start_run):
            kwargs: dict[str, Any] = {}
            supports_on_result = False
            try:
                parameters = inspect.signature(start_run).parameters
            except (TypeError, ValueError):
                parameters = {}

            if "resume_latest_thread" in parameters:
                kwargs["resume_latest_thread"] = resume_latest_thread
            if "on_result" in parameters and on_result is not None:
                kwargs["on_result"] = on_result
                supports_on_result = True

            handle = await start_run(request, **kwargs)
            if supports_on_result:
                self.track_future(
                    asyncio.create_task(
                        handle.wait(),
                        name=f"gatekeeper-wait-{request.trigger.value}",
                    )
                )
                return handle

            async def wait_and_apply() -> GatekeeperRunResult:
                result = await handle.wait()
                return await self._maybe_invoke_callback(on_result, result)

            future = asyncio.create_task(
                wait_and_apply(),
                name=f"gatekeeper-{request.trigger.value}",
            )
            return self.future_handle(future)

        async def run_and_apply() -> GatekeeperRunResult:
            result = await self.run_request(
                request,
                resume_latest_thread=resume_latest_thread,
            )
            return await self._maybe_invoke_callback(on_result, result)

        future = asyncio.create_task(
            run_and_apply(),
            name=f"gatekeeper-{request.trigger.value}",
        )
        return self.future_handle(future)

    async def run_request(
        self,
        request: GatekeeperRequest,
        resume_latest_thread: bool | None = None,
    ) -> GatekeeperRunResult:
        if self._uses_managed_runtime():
            handle = await self.start_request(
                request,
                resume_latest_thread=resume_latest_thread,
            )
            agent_instance = getattr(handle, "agent_instance", None)
            if not isinstance(agent_instance, ManagedAgentInstance):
                agent_record = getattr(handle, "agent_record", None)
                if agent_record is None:
                    raise RuntimeError("Managed Gatekeeper handle is missing agent instance context")
                agent_instance = self._managed_instance(
                    role=agent_record.identity.role,
                    scope_type="project",
                    scope_id="project",
                )
            execution_result = await agent_instance.wait_for_run()
            result = execution_result.normalized_result
            if result is None:
                raise RuntimeError(
                    f"Gatekeeper run {handle.agent_record.identity.run_id} did not produce a normalized result"
                )
            return result

        run_gatekeeper = self.gatekeeper.run
        try:
            parameters = inspect.signature(run_gatekeeper).parameters
        except (TypeError, ValueError):
            parameters = {}

        if "resume_latest_thread" in parameters:
            return await run_gatekeeper(request, resume_latest_thread=resume_latest_thread)
        return await run_gatekeeper(request)

    async def answer_question(self, question: str, answer: str) -> GatekeeperRunResult:
        request = GatekeeperRequest(
            trigger=GatekeeperTrigger.USER_CONVERSATION,
            trigger_description=f"Question: {question}\nUser Answer: {answer}",
            agent_summary=answer,
        )
        return await self.run_request(request, resume_latest_thread=True)

    async def apply_result_async(self, result: GatekeeperRunResult) -> None:
        self.state_store.apply_gatekeeper_result(result)
        self.roadmap_service.reload(
            project_name=self.roadmap_service.project_name,
            concurrency_limit=self.state_store.state.concurrency_limit,
        )
        self.roadmap_service.persist()
        self.workflow_service.maybe_complete_workflow()
        self.state_store.refresh()


def _extract_provider_thread_id(resume_cursor: object) -> str | None:
    if not isinstance(resume_cursor, dict):
        return None
    thread_id = resume_cursor.get("threadId")
    return thread_id if isinstance(thread_id, str) and thread_id else None


__all__ = ["AsyncGatekeeperHandle", "GatekeeperRuntimeService"]
