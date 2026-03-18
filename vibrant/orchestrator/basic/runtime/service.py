"""Generic agent runtime orchestration service."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Sequence
from pathlib import Path
from dataclasses import dataclass
from uuid import uuid4

from vibrant.agents.runtime import AgentHandle, AgentRecordCallback, AgentRuntime, BaseAgentRuntime, ProviderThreadHandle
from vibrant.models.agent import AgentRunRecord
from vibrant.providers.base import CanonicalEvent
from vibrant.providers.invocation import ProviderInvocationPlan

from ...types import CanonicalEventHandler, RuntimeExecutionResult, RuntimeHandleSnapshot


@dataclass(slots=True)
class _RuntimeSubscription:
    callback: CanonicalEventHandler
    agent_id: str | None = None
    run_id: str | None = None
    event_types: frozenset[str] | None = None


@dataclass(slots=True)
class _LiveRun:
    agent_record: AgentRunRecord
    runtime: AgentRuntime
    handle: AgentHandle
    on_record_updated: AgentRecordCallback | None = None
    sequence: int = 0


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
        runtime_factory: Callable[[AgentRunRecord], AgentRuntime] | None = None,
    ) -> None:
        self._runtime_factory = runtime_factory
        self._runs: dict[str, _LiveRun] = {}
        self._active_runs_by_agent_id: dict[str, str] = {}
        self._subscriptions: list[_RuntimeSubscription] = []

    async def start_run(
        self,
        *,
        agent_record: AgentRunRecord,
        prompt: str,
        cwd: Path | None = None,
        resume_thread_id: str | None = None,
        on_record_updated: AgentRecordCallback | None = None,
        runtime: AgentRuntime | None = None,
        invocation_plan: ProviderInvocationPlan | None = None,
    ) -> AgentHandle:
        existing_run_id = self._active_runs_by_agent_id.get(agent_record.identity.agent_id)
        if existing_run_id is not None:
            existing_live_run = self._runs.get(existing_run_id)
            if existing_live_run is not None and not existing_live_run.handle.done:
                raise RuntimeError(f"Agent {agent_record.identity.agent_id} already has an active run")
        resolved_runtime = runtime or self._build_runtime(agent_record)
        self._attach_event_bridge(resolved_runtime, agent_record)
        handle = await resolved_runtime.start(
            agent_record=agent_record,
            prompt=prompt,
            cwd=cwd,
            resume_thread_id=resume_thread_id,
            on_record_updated=on_record_updated,
            invocation_plan=invocation_plan,
        )
        self._remember_live_run(
            run_id=agent_record.identity.run_id,
            agent_id=agent_record.identity.agent_id,
            live_run=_LiveRun(
                agent_record=agent_record,
                runtime=resolved_runtime,
                handle=handle,
                on_record_updated=on_record_updated,
            ),
        )
        return handle

    async def resume_run(
        self,
        *,
        agent_record: AgentRunRecord,
        prompt: str,
        provider_thread: ProviderThreadHandle,
        cwd: Path | None = None,
        on_record_updated: AgentRecordCallback | None = None,
        runtime: AgentRuntime | None = None,
        invocation_plan: ProviderInvocationPlan | None = None,
    ) -> AgentHandle:
        existing_run_id = self._active_runs_by_agent_id.get(agent_record.identity.agent_id)
        if existing_run_id is not None:
            existing_live_run = self._runs.get(existing_run_id)
            if existing_live_run is not None and not existing_live_run.handle.done:
                raise RuntimeError(f"Agent {agent_record.identity.agent_id} already has an active run")
        resolved_runtime = runtime or self._build_runtime(agent_record)
        self._attach_event_bridge(resolved_runtime, agent_record)
        handle = await resolved_runtime.resume_run(
            agent_record=agent_record,
            prompt=prompt,
            provider_thread=provider_thread,
            cwd=cwd,
            on_record_updated=on_record_updated,
            invocation_plan=invocation_plan,
        )
        self._remember_live_run(
            run_id=agent_record.identity.run_id,
            agent_id=agent_record.identity.agent_id,
            live_run=_LiveRun(
                agent_record=agent_record,
                runtime=resolved_runtime,
                handle=handle,
                on_record_updated=on_record_updated,
            ),
        )
        return handle

    async def wait_for_run(
        self,
        run_id: str,
    ) -> RuntimeExecutionResult:
        live_run = self._resolve_live_run(run_id)
        result = await live_run.handle.wait()
        provider_thread = result.provider_thread
        execution_result = RuntimeExecutionResult(
            run_id=result.run_id,
            agent_id=result.agent_id,
            role=result.role,
            status=result.status,
            summary=result.summary,
            error=result.error,
            awaiting_input=result.awaiting_input,
            provider_events_ref=result.provider_events_ref,
            provider_thread_id=provider_thread.thread_id,
            input_requests=list(result.input_requests),
        )
        if live_run.handle.done:
            self._forget_live_run(run_id, live_run.agent_record.identity.agent_id)
        return execution_result

    async def interrupt_run(self, run_id: str) -> RuntimeHandleSnapshot:
        live_run = self._resolve_live_run(run_id)
        await live_run.handle.interrupt()
        return self.snapshot_handle(live_run.agent_record.identity.run_id)

    async def kill_run(self, run_id: str) -> RuntimeHandleSnapshot:
        live_run = self._resolve_live_run(run_id)
        await live_run.handle.kill()
        return self.snapshot_handle(live_run.agent_record.identity.run_id)

    def snapshot_handle(self, run_id: str) -> RuntimeHandleSnapshot:
        live_run = self._resolve_live_run(run_id)
        provider_thread = live_run.handle.provider_thread
        return RuntimeHandleSnapshot(
            agent_id=live_run.agent_record.identity.agent_id,
            run_id=run_id,
            state=live_run.handle.state.value,
            provider_thread_id=provider_thread.thread_id,
            awaiting_input=live_run.handle.awaiting_input,
            input_requests=live_run.handle.input_requests,
        )

    def annotate_run(self, run_id: str, *, stop_reason: str | None = None) -> None:
        live_run = self._resolve_live_run(run_id)
        live_run.agent_record.lifecycle.stop_reason = stop_reason
        callback = live_run.on_record_updated
        if callback is not None:
            callback(live_run.agent_record)

    def live_run_ids(self) -> set[str]:
        return {
            run_id
            for run_id, live_run in self._runs.items()
            if not live_run.handle.done
        }

    def subscribe_canonical_events(
        self,
        callback: CanonicalEventHandler,
        *,
        agent_id: str | None = None,
        run_id: str | None = None,
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
            run_id=run_id,
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

    def _build_runtime(self, agent_record: AgentRunRecord) -> AgentRuntime:
        if self._runtime_factory is None:
            raise ValueError("AgentRuntimeService requires a runtime or runtime_factory")
        runtime = self._runtime_factory(agent_record)
        if not isinstance(runtime, BaseAgentRuntime) and not hasattr(runtime, "start"):
            raise TypeError(f"Unsupported runtime instance: {type(runtime)!r}")
        return runtime

    def _attach_event_bridge(self, runtime: AgentRuntime, agent_record: AgentRunRecord) -> None:
        # TODO: need to proof its correctness
        agent = getattr(runtime, "_agent", None)
        if agent is None or getattr(agent, "_orchestrator_runtime_bridge", False):
            return

        original_callback = getattr(agent, "on_canonical_event", None)

        async def _bridge(event: CanonicalEvent) -> None:
            normalized = self._normalize_event(agent_record, event)
            await self._publish(normalized)
            if original_callback is not None:
                result = original_callback(event)
                if inspect.isawaitable(result):
                    await result

        setattr(agent, "_orchestrator_runtime_bridge", True)
        agent.on_canonical_event = _bridge

    def _normalize_event(self, agent_record: AgentRunRecord, event: CanonicalEvent) -> CanonicalEvent:
        live_run = self._runs.get(agent_record.identity.run_id)
        sequence = 1
        if live_run is not None:
            live_run.sequence += 1
            sequence = live_run.sequence

        normalized: CanonicalEvent = dict(event)
        normalized.setdefault("agent_id", agent_record.identity.agent_id)
        normalized.setdefault("run_id", agent_record.identity.run_id)
        normalized.setdefault("role", agent_record.identity.role)
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
        if subscription.run_id is not None and event.get("run_id") != subscription.run_id:
            return False
        if subscription.event_types is not None and str(event.get("type") or "") not in subscription.event_types:
            return False
        return True

    def _remember_live_run(self, *, run_id: str, agent_id: str, live_run: _LiveRun) -> None:
        self._runs[run_id] = live_run
        self._active_runs_by_agent_id[agent_id] = run_id

    def _forget_live_run(self, run_id: str, agent_id: str) -> None:
        self._runs.pop(run_id, None)
        if self._active_runs_by_agent_id.get(agent_id) == run_id:
            self._active_runs_by_agent_id.pop(agent_id, None)

    def _resolve_live_run(self, run_id: str) -> _LiveRun:
        try:
            live_run = self._runs[run_id]
        except KeyError as exc:
            raise KeyError(run_id) from exc
        return live_run
