"""Execution runtime service."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from vibrant.agents.runtime import (
    AgentHandle,
    AgentRecordCallback,
    AgentRuntime,
    InputRequest,
    NormalizedRunResult,
    ProviderResumeHandle,
    ProviderThreadHandle,
    RunState,
)
from vibrant.models.agent import AgentRunRecord
from vibrant.orchestrator.execution.git_manager import GitWorktreeInfo

from ..types import (
    AgentSnapshotIdentity,
    AgentSnapshotOutcome,
    AgentSnapshotProvider,
    AgentSnapshotRuntime,
    RuntimeExecutionResult,
)
from .registry import AgentRegistry

RuntimeFactory = Callable[[AgentRunRecord], AgentRuntime]


@dataclass(slots=True)
class RuntimeHandleSnapshot:
    """Serializable view of an in-flight or completed agent handle."""

    identity: AgentSnapshotIdentity
    runtime: AgentSnapshotRuntime
    outcome: AgentSnapshotOutcome = field(default_factory=AgentSnapshotOutcome)
    provider: AgentSnapshotProvider = field(default_factory=AgentSnapshotProvider)


class AgentRuntimeService:
    """Own provider adapter session/thread/turn execution details."""

    def __init__(
        self,
        *,
        agent_registry: AgentRegistry,
        agent_runtime: AgentRuntime | RuntimeFactory,
    ) -> None:
        self.agent_registry = agent_registry
        self._agent_runtime = agent_runtime
        self._handles: dict[str, AgentHandle] = {}

    @property
    def supports_handles(self) -> bool:
        return True

    def _make_record_callback(self) -> AgentRecordCallback:
        registry = self.agent_registry

        def _persist(record: AgentRunRecord) -> None:
            registry.upsert(record)

        return _persist

    def _resolve_runtime(self, agent_record: AgentRunRecord) -> AgentRuntime:
        runtime = self._agent_runtime
        if runtime is None:
            raise RuntimeError("No protocol-based AgentRuntime configured")
        if hasattr(runtime, "start"):
            return runtime  # type: ignore[return-value]
        candidate = runtime(agent_record)
        if not hasattr(candidate, "start"):
            raise TypeError("agent_runtime factory must return an AgentRuntime-compatible object")
        return candidate

    def get_handle(self, agent_id: str) -> AgentHandle | None:
        """Return the currently tracked handle for a stable agent, if one exists."""
        return self._handles.get(agent_id)

    def release_handle(self, agent_id: str) -> AgentHandle | None:
        """Stop tracking a handle in the runtime service."""
        return self._handles.pop(agent_id, None)

    def _agent_id_for_handle(self, handle: AgentHandle) -> str | None:
        for candidate_agent_id, candidate_handle in self._handles.items():
            if candidate_handle is handle:
                return candidate_agent_id
        return None

    def _resolve_provider_thread(
        self,
        *,
        agent_record: AgentRunRecord,
        provider_thread: ProviderThreadHandle | None,
    ) -> ProviderThreadHandle:
        if provider_thread is not None:
            return provider_thread
        persisted = self.agent_registry.provider_thread_handle(agent_record.identity.agent_id)
        if persisted is not None:
            return persisted
        return ProviderResumeHandle.from_provider_metadata(agent_record.provider) or ProviderResumeHandle(
            kind=agent_record.provider.kind
        )

    def snapshot_handle(
        self,
        *,
        agent_id: str | None = None,
        handle: AgentHandle | None = None,
        agent_record: AgentRunRecord | None = None,
    ) -> RuntimeHandleSnapshot:
        """Return a serializable snapshot for a tracked handle."""
        if handle is None:
            if agent_id is None:
                raise ValueError("agent_id or handle is required")
            handle = self.get_handle(agent_id)
            if handle is None:
                raise KeyError(f"Unknown agent handle: {agent_id}")
        if agent_record is None:
            key = agent_id or self._agent_id_for_handle(handle)
            if key is None:
                raise ValueError("agent_id or agent_record is required")
            agent_record = self.agent_registry.get(key)
        if agent_record is None:
            raise KeyError(f"Unknown agent record: {agent_id}")

        provider_thread = handle.provider_thread
        if provider_thread.empty:
            provider_thread = self._resolve_provider_thread(agent_record=agent_record, provider_thread=None)
        return RuntimeHandleSnapshot(
            identity=AgentSnapshotIdentity(
                agent_id=agent_record.identity.agent_id,
                run_id=agent_record.identity.run_id,
                task_id=agent_record.identity.task_id,
                role=agent_record.identity.role,
            ),
            runtime=AgentSnapshotRuntime(
                status=agent_record.lifecycle.status.value,
                state=handle.state.value,
                has_handle=True,
                active=True,
                done=handle.done,
                awaiting_input=handle.awaiting_input,
                pid=agent_record.lifecycle.pid,
                started_at=agent_record.lifecycle.started_at,
                finished_at=agent_record.lifecycle.finished_at,
                input_requests=handle.input_requests,
            ),
            outcome=AgentSnapshotOutcome(
                summary=agent_record.outcome.summary,
                error=agent_record.outcome.error,
            ),
            provider=AgentSnapshotProvider(
                thread_id=provider_thread.thread_id,
                thread_path=provider_thread.thread_path,
                resume_cursor=provider_thread.resume_cursor,
                native_event_log=agent_record.provider.native_event_log,
                canonical_event_log=agent_record.provider.canonical_event_log,
            ),
        )

    def list_handle_snapshots(self, *, include_completed: bool = True) -> list[RuntimeHandleSnapshot]:
        """List tracked handle snapshots in stable agent-id order."""
        snapshots: list[RuntimeHandleSnapshot] = []
        for agent_id in sorted(self._handles):
            handle = self._handles[agent_id]
            record = self.agent_registry.get(agent_id)
            if record is None:
                continue
            snapshot = self.snapshot_handle(handle=handle, agent_record=record)
            if not include_completed and snapshot.runtime.done and not snapshot.runtime.awaiting_input:
                continue
            snapshots.append(snapshot)
        return snapshots

    async def start_run(
        self,
        *,
        agent_record: AgentRunRecord,
        prompt: str,
        cwd: str,
        resume_thread_id: str | None = None,
        increment_spawn: bool = True,
    ) -> AgentHandle:
        """Start an agent run and return the durable handle immediately."""
        existing = self.get_handle(agent_record.identity.agent_id)
        if existing is not None and not existing.done:
            raise RuntimeError(f"Agent {agent_record.identity.agent_id} already has an active run")

        runtime = self._resolve_runtime(agent_record)
        agent_record.lifecycle.started_at = datetime.now(timezone.utc)
        self.agent_registry.upsert(agent_record, increment_spawn=increment_spawn)
        handle = await runtime.start(
            agent_record=agent_record,
            prompt=prompt,
            cwd=cwd,
            resume_thread_id=resume_thread_id,
            on_record_updated=self._make_record_callback(),
        )
        self._handles[agent_record.identity.agent_id] = handle
        return handle

    async def resume_run(
        self,
        *,
        agent_record: AgentRunRecord,
        prompt: str,
        cwd: str,
        provider_thread: ProviderThreadHandle | None = None,
    ) -> AgentHandle:
        """Resume an existing handle-backed run using durable provider metadata."""
        existing = self.get_handle(agent_record.identity.agent_id)
        if existing is not None and not existing.done:
            raise RuntimeError(f"Agent {agent_record.identity.agent_id} already has an active run")

        runtime = self._resolve_runtime(agent_record)
        provider_thread = self._resolve_provider_thread(
            agent_record=agent_record,
            provider_thread=provider_thread,
        )
        if not provider_thread.resumable:
            raise ValueError(f"Agent {agent_record.identity.agent_id} has no resumable provider thread")
        self.agent_registry.upsert(agent_record, increment_spawn=False)
        handle = await runtime.resume_run(
            agent_record=agent_record,
            prompt=prompt,
            provider_thread=provider_thread,
            cwd=cwd,
            on_record_updated=self._make_record_callback(),
        )
        self._handles[agent_record.identity.agent_id] = handle
        return handle

    async def start_task(
        self,
        *,
        worktree: GitWorktreeInfo,
        prompt: str,
        agent_record: AgentRunRecord,
        resume_thread_id: str | None = None,
    ) -> AgentHandle:
        """Start a worktree-scoped agent run."""
        return await self.start_run(
            agent_record=agent_record,
            prompt=prompt,
            cwd=str(worktree.path),
            resume_thread_id=resume_thread_id,
            increment_spawn=True,
        )

    async def resume_task(
        self,
        *,
        worktree: GitWorktreeInfo,
        prompt: str,
        agent_record: AgentRunRecord,
        provider_thread: ProviderThreadHandle | None = None,
    ) -> AgentHandle:
        """Resume a worktree-scoped agent run."""
        return await self.resume_run(
            agent_record=agent_record,
            prompt=prompt,
            cwd=str(worktree.path),
            provider_thread=provider_thread,
        )

    async def respond_to_request(
        self,
        *,
        agent_id: str | None = None,
        handle: AgentHandle | None = None,
        request_id: int | str,
        result: Any | None = None,
        error: dict[str, Any] | None = None,
    ) -> RuntimeHandleSnapshot:
        """Forward a request response through the tracked handle."""
        if handle is None:
            if agent_id is None:
                raise ValueError("agent_id or handle is required")
            handle = self.get_handle(agent_id)
            if handle is None:
                raise KeyError(f"Unknown agent handle: {agent_id}")
        await handle.respond_to_request(request_id, result=result, error=error)
        agent_record = self.agent_registry.get(agent_id or self._agent_id_for_handle(handle) or "")
        return self.snapshot_handle(handle=handle, agent_record=agent_record)

    async def interrupt_run(
        self,
        *,
        agent_id: str | None = None,
        handle: AgentHandle | None = None,
    ) -> RuntimeHandleSnapshot:
        """Interrupt a tracked run and return the updated snapshot."""
        if handle is None:
            if agent_id is None:
                raise ValueError("agent_id or handle is required")
            handle = self.get_handle(agent_id)
            if handle is None:
                raise KeyError(f"Unknown agent handle: {agent_id}")
        await handle.interrupt()
        agent_record = self.agent_registry.get(agent_id or self._agent_id_for_handle(handle) or "")
        return self.snapshot_handle(handle=handle, agent_record=agent_record)

    async def kill_run(
        self,
        *,
        agent_id: str | None = None,
        handle: AgentHandle | None = None,
    ) -> RuntimeHandleSnapshot:
        """Forcefully tear down a tracked run and return the updated snapshot."""
        if handle is None:
            if agent_id is None:
                raise ValueError("agent_id or handle is required")
            handle = self.get_handle(agent_id)
            if handle is None:
                raise KeyError(f"Unknown agent handle: {agent_id}")
        await handle.kill()
        agent_record = self.agent_registry.get(agent_id or self._agent_id_for_handle(handle) or "")
        return self.snapshot_handle(handle=handle, agent_record=agent_record)

    async def wait_for_run(
        self,
        *,
        agent_id: str | None = None,
        handle: AgentHandle | None = None,
        release_terminal: bool = True,
    ) -> RuntimeExecutionResult:
        """Wait for a tracked handle and return the orchestrator-facing runtime result."""
        if handle is None:
            if agent_id is None:
                raise ValueError("agent_id or handle is required")
            handle = self.get_handle(agent_id)
            if handle is None:
                raise KeyError(f"Unknown agent handle: {agent_id}")
        normalized = await handle.wait()
        snapshot = self.snapshot_handle(handle=handle, agent_record=normalized.agent_record)
        result = _normalized_to_execution_result(normalized, snapshot=snapshot)
        if release_terminal and not result.awaiting_input:
            self.release_handle(normalized.agent_record.identity.agent_id)
        return result


def _normalized_to_execution_result(
    result: NormalizedRunResult,
    *,
    snapshot: RuntimeHandleSnapshot | None = None,
) -> RuntimeExecutionResult:
    """Bridge a ``NormalizedRunResult`` to the orchestrator's runtime result type."""
    provider_thread = result.provider_thread
    if snapshot is not None:
        provider_thread = ProviderResumeHandle(
            thread_id=snapshot.provider.thread_id,
            thread_path=snapshot.provider.thread_path,
            resume_cursor=snapshot.provider.resume_cursor,
        )
    return RuntimeExecutionResult(
        agent_record=result.agent_record,
        events=result.events,
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
