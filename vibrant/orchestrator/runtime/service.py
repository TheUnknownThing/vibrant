"""Generic agent runtime orchestration service."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from vibrant.agents.runtime import AgentHandle, AgentRuntime, BaseAgentRuntime
from vibrant.models.agent import AgentRecord
from vibrant.providers.base import CanonicalEvent

from ..types import CanonicalEventHandler, RuntimeExecutionResult, RuntimeHandleSnapshot


@dataclass(slots=True)
class _RuntimeSubscription:
    callback: CanonicalEventHandler
    agent_id: str | None = None
    task_id: str | None = None
    event_types: frozenset[str] | None = None


@dataclass(slots=True)
class _LiveRun:
    agent_record: AgentRecord
    runtime: AgentRuntime
    handle: AgentHandle
    sequence: int = 0
    events: list[CanonicalEvent] = field(default_factory=list)


class _EventSubscriptionHandle:
    def __init__(self, close_callback: Callable[[], None]) -> None:
        self._close_callback = close_callback
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._close_callback()


class AgentRuntimeService:
    """Manage live agent handles and publish normalized canonical events."""

    def __init__(
        self,
        runtime_factory: Callable[[AgentRecord], AgentRuntime] | None = None,
    ) -> None:
        self._runtime_factory = runtime_factory
        self._runs: dict[str, _LiveRun] = {}
        self._subscriptions: list[_RuntimeSubscription] = []

    async def start_run(
        self,
        *,
        agent_record: AgentRecord,
        prompt: str,
        cwd: str | None = None,
        resume_thread_id: str | None = None,
        on_record_updated: Callable[[AgentRecord], Any] | None = None,
        runtime: AgentRuntime | None = None,
    ) -> AgentHandle:
        resolved_runtime = runtime or self._build_runtime(agent_record)
        self._attach_event_bridge(resolved_runtime, agent_record)
        handle = await resolved_runtime.start(
            agent_record=agent_record,
            prompt=prompt,
            cwd=cwd,
            resume_thread_id=resume_thread_id,
            on_record_updated=on_record_updated,
        )
        self._runs[agent_record.identity.agent_id] = _LiveRun(
            agent_record=agent_record,
            runtime=resolved_runtime,
            handle=handle,
        )
        return handle

    async def resume_run(
        self,
        *,
        agent_record: AgentRecord,
        prompt: str,
        provider_thread: Any,
        cwd: str | None = None,
        on_record_updated: Callable[[AgentRecord], Any] | None = None,
        runtime: AgentRuntime | None = None,
    ) -> AgentHandle:
        resolved_runtime = runtime or self._build_runtime(agent_record)
        self._attach_event_bridge(resolved_runtime, agent_record)
        handle = await resolved_runtime.resume_run(
            agent_record=agent_record,
            prompt=prompt,
            provider_thread=provider_thread,
            cwd=cwd,
            on_record_updated=on_record_updated,
        )
        self._runs[agent_record.identity.agent_id] = _LiveRun(
            agent_record=agent_record,
            runtime=resolved_runtime,
            handle=handle,
        )
        return handle

    async def wait_for_run(self, agent_id: str) -> RuntimeExecutionResult:
        live_run = self._runs[agent_id]
        result = await live_run.handle.wait()
        provider_thread = result.provider_thread
        execution_result = RuntimeExecutionResult(
            agent_record=result.agent_record,
            events=list(live_run.events),
            summary=result.summary,
            error=result.error,
            turn_result=result.turn_result,
            state=result.state,
            awaiting_input=result.awaiting_input,
            provider_thread_id=provider_thread.thread_id,
            provider_thread_path=provider_thread.thread_path,
            provider_resume_cursor=provider_thread.resume_cursor,
            input_requests=list(result.input_requests),
            normalized_result=result,
        )
        if live_run.handle.done:
            self._runs.pop(agent_id, None)
        return execution_result

    async def interrupt_run(self, agent_id: str) -> RuntimeHandleSnapshot:
        live_run = self._runs[agent_id]
        await live_run.handle.interrupt()
        return self.snapshot_handle(agent_id)

    async def kill_run(self, agent_id: str) -> RuntimeHandleSnapshot:
        live_run = self._runs[agent_id]
        await live_run.handle.kill()
        return self.snapshot_handle(agent_id)

    def snapshot_handle(self, agent_id: str) -> RuntimeHandleSnapshot:
        live_run = self._runs[agent_id]
        provider_thread = live_run.handle.provider_thread
        return RuntimeHandleSnapshot(
            agent_id=agent_id,
            state=live_run.handle.state.value,
            provider_thread_id=provider_thread.thread_id,
            awaiting_input=live_run.handle.awaiting_input,
            input_requests=live_run.handle.input_requests,
        )

    def subscribe_canonical_events(
        self,
        callback: CanonicalEventHandler,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        event_types: Sequence[str] | None = None,
    ) -> _EventSubscriptionHandle:
        normalized_types = (
            frozenset(event_type for event_type in event_types if event_type)
            if event_types is not None
            else None
        )
        subscription = _RuntimeSubscription(
            callback=callback,
            agent_id=agent_id,
            task_id=task_id,
            event_types=normalized_types,
        )
        self._subscriptions.append(subscription)
        return _EventSubscriptionHandle(lambda: self._unsubscribe(subscription))

    async def ingest_event(self, event: CanonicalEvent) -> None:
        await self._publish(event)

    def _unsubscribe(self, subscription: _RuntimeSubscription) -> None:
        try:
            self._subscriptions.remove(subscription)
        except ValueError:
            return

    def _build_runtime(self, agent_record: AgentRecord) -> AgentRuntime:
        if self._runtime_factory is None:
            raise ValueError("AgentRuntimeService requires a runtime or runtime_factory")
        runtime = self._runtime_factory(agent_record)
        if not isinstance(runtime, BaseAgentRuntime) and not hasattr(runtime, "start"):
            raise TypeError(f"Unsupported runtime instance: {type(runtime)!r}")
        return runtime

    def _attach_event_bridge(self, runtime: AgentRuntime, agent_record: AgentRecord) -> None:
        agent = getattr(runtime, "_agent", None)
        if agent is None or getattr(agent, "_orchestrator_runtime_bridge", False):
            return

        original_callback = getattr(agent, "on_canonical_event", None)

        async def _bridge(event: CanonicalEvent) -> None:
            normalized = self._normalize_event(agent_record, event)
            live_run = self._runs.get(agent_record.identity.agent_id)
            if live_run is not None:
                live_run.events.append(normalized)
            await self._publish(normalized)
            if original_callback is not None:
                result = original_callback(event)
                if inspect.isawaitable(result):
                    await result

        setattr(agent, "_orchestrator_runtime_bridge", True)
        agent.on_canonical_event = _bridge

    def _normalize_event(self, agent_record: AgentRecord, event: CanonicalEvent) -> CanonicalEvent:
        live_run = self._runs.get(agent_record.identity.agent_id)
        sequence = 1
        if live_run is not None:
            live_run.sequence += 1
            sequence = live_run.sequence

        normalized: dict[str, Any] = dict(event)
        normalized.setdefault("agent_id", agent_record.identity.agent_id)
        normalized.setdefault("task_id", agent_record.identity.task_id)
        normalized.setdefault("provider", agent_record.provider.kind)
        normalized.setdefault("origin", "provider")
        normalized.setdefault("timestamp", normalized.get("timestamp"))
        normalized.setdefault("event_id", str(uuid4()))
        normalized["sequence"] = sequence
        return normalized

    async def _publish(self, event: CanonicalEvent) -> None:
        for subscription in tuple(self._subscriptions):
            if not self._matches(subscription, event):
                continue
            result = subscription.callback(event)
            if inspect.isawaitable(result):
                await result

    @staticmethod
    def _matches(subscription: _RuntimeSubscription, event: CanonicalEvent) -> bool:
        if subscription.agent_id is not None and event.get("agent_id") != subscription.agent_id:
            return False
        if subscription.task_id is not None and event.get("task_id") != subscription.task_id:
            return False
        if subscription.event_types is not None and str(event.get("type") or "") not in subscription.event_types:
            return False
        return True
