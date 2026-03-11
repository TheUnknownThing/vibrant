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
from vibrant.models.agent import AgentRecord, AgentStatus, AgentType

from .agents.registry import AgentRegistry
from .agents.runtime import AgentRuntimeService
from .artifacts.roadmap import RoadmapService
from .artifacts.workflow import WorkflowService
from .state.store import StateStore

GatekeeperResultCallback = Callable[[GatekeeperRunResult], Any | Awaitable[Any]]
_MIN_TIME = datetime.min.replace(tzinfo=timezone.utc)


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
            record.type is AgentType.GATEKEEPER
            and record.status not in AgentRecord.TERMINAL_STATUSES
            and record.status is not AgentStatus.AWAITING_INPUT
            for record in self.agent_registry.list_records()
        )
        return managed_busy or any(not future.done() for future in self._active_futures)

    def _uses_managed_runtime(self) -> bool:
        return isinstance(self.gatekeeper, Gatekeeper) and self.runtime_service.supports_handles

    def _latest_thread_id(self) -> str | None:
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
        agent_id: str,
        callback: GatekeeperResultCallback,
    ) -> GatekeeperRunResult:
        execution_result = await self.runtime_service.wait_for_run(agent_id=agent_id)
        result = execution_result.normalized_result
        if result is None:
            raise RuntimeError(f"Gatekeeper run {agent_id} did not produce a normalized result")
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
            agent_record = self.gatekeeper.build_agent_record(request)
            agent_record.prompt_used = prompt

            should_resume = (
                resume_latest_thread
                if resume_latest_thread is not None
                else request.trigger is GatekeeperTrigger.USER_CONVERSATION
            )
            resume_thread_id = self._latest_thread_id() if should_resume else None
            handle = await self.runtime_service.start_run(
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
                    self._forward_managed_result(agent_id=agent_record.agent_id, callback=on_result),
                    name=f"gatekeeper-{request.trigger.value}-{agent_record.agent_id}",
                )
                self.track_future(future)

            return handle

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
            execution_result = await self.runtime_service.wait_for_run(handle=handle)
            result = execution_result.normalized_result
            if result is None:
                raise RuntimeError(f"Gatekeeper run {handle.agent_record.agent_id} did not produce a normalized result")
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
