"""First-class in-memory agent-instance abstraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from vibrant.agents.runtime import AgentHandle, ProviderThreadHandle
from vibrant.models.agent import AgentInstanceRecord, AgentRunRecord, ProviderResumeHandle

from ..types import (
    AgentSnapshotIdentity,
    AgentSnapshotOutcome,
    AgentSnapshotProvider,
    AgentSnapshotRuntime,
    AgentSnapshotWorkspace,
    OrchestratorAgentSnapshot,
    RuntimeExecutionResult,
)
from .output_projection import AgentOutputProjectionService
from .registry import AgentRegistry
from .runtime import AgentRuntimeService


@dataclass(frozen=True, slots=True)
class StartedAgentRun:
    """Prepared run record plus its live runtime handle."""

    agent: "ManagedAgentInstance"
    agent_record: AgentRunRecord
    handle: AgentHandle


@runtime_checkable
class AgentInstance(Protocol):
    """Role-neutral lifecycle surface for one stable agent instance."""

    @property
    def agent_id(self) -> str: ...

    @property
    def role(self) -> str: ...

    @property
    def scope_type(self) -> str: ...

    @property
    def scope_id(self) -> str | None: ...

    @property
    def record(self) -> AgentInstanceRecord: ...

    def latest_run(self) -> AgentRunRecord | None: ...

    def active_run(self) -> AgentRunRecord | None: ...

    def provider_thread_handle(self) -> ProviderThreadHandle | None: ...

    @property
    def persistent_thread(self) -> bool: ...

    @property
    def supports_interactive_requests(self) -> bool: ...

    def get_handle(self) -> AgentHandle | None: ...

    def snapshot(self, *, record: AgentRunRecord | None = None) -> OrchestratorAgentSnapshot: ...

    def create_run_record(
        self,
        *,
        task_id: str,
        branch: str | None,
        worktree_path: str | None,
        prompt: str | None = None,
        skills: list[str] | None = None,
        retry_count: int = 0,
        max_retries: int = 3,
    ) -> AgentRunRecord: ...

    async def start_run(
        self,
        *,
        agent_record: AgentRunRecord,
        prompt: str,
        cwd: str,
        resume_thread_id: str | None = None,
        increment_spawn: bool = True,
    ) -> AgentHandle: ...

    async def resume_run(
        self,
        *,
        agent_record: AgentRunRecord,
        prompt: str,
        cwd: str,
        provider_thread: ProviderThreadHandle | None = None,
    ) -> AgentHandle: ...

    async def wait_for_run(self, *, release_terminal: bool = True) -> RuntimeExecutionResult: ...

    async def respond_to_request(
        self,
        request_id: int | str,
        *,
        result: object | None = None,
        error: dict[str, object] | None = None,
    ) -> OrchestratorAgentSnapshot: ...

    async def interrupt(self) -> OrchestratorAgentSnapshot: ...

    async def kill(self) -> OrchestratorAgentSnapshot: ...


class ManagedAgentInstance:
    """Concrete in-memory facade for one stable agent instance."""

    def __init__(
        self,
        *,
        agent_id: str,
        agent_registry: AgentRegistry,
        runtime_service: AgentRuntimeService,
        output_service: AgentOutputProjectionService | None = None,
        seed_record: AgentInstanceRecord | None = None,
    ) -> None:
        self._agent_id = agent_id
        self._agent_registry = agent_registry
        self._runtime_service = runtime_service
        self._output_service = output_service
        self._seed_record = seed_record

    @classmethod
    def from_record(
        cls,
        record: AgentInstanceRecord,
        *,
        agent_registry: AgentRegistry,
        runtime_service: AgentRuntimeService,
        output_service: AgentOutputProjectionService | None = None,
    ) -> "ManagedAgentInstance":
        return cls(
            agent_id=record.identity.agent_id,
            agent_registry=agent_registry,
            runtime_service=runtime_service,
            output_service=output_service,
            seed_record=record,
        )

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def role(self) -> str:
        return self.record.identity.role

    @property
    def scope_type(self) -> str:
        return self.record.scope.scope_type

    @property
    def scope_id(self) -> str | None:
        return self.record.scope.scope_id

    @property
    def record(self) -> AgentInstanceRecord:
        instance = self._agent_registry.get_instance(self._agent_id)
        if instance is not None:
            self._seed_record = instance
            return instance

        latest_run = self._agent_registry.get(self._agent_id)
        if latest_run is not None:
            instance = self._agent_registry.ensure_instance_for_run(latest_run)
            self._seed_record = instance
            return instance

        if self._seed_record is not None:
            return self._seed_record
        raise KeyError(f"Unknown agent instance: {self._agent_id}")

    def latest_run(self) -> AgentRunRecord | None:
        latest_run_id = self.record.latest_run_id
        if latest_run_id is not None:
            latest_run = self._agent_registry.get_run(latest_run_id)
            if latest_run is not None:
                return latest_run
        return self._agent_registry.get(self._agent_id)

    def active_run(self) -> AgentRunRecord | None:
        active_run_id = self.record.active_run_id
        if active_run_id is None:
            return None
        return self._agent_registry.get_run(active_run_id)

    def provider_thread_handle(self) -> ProviderThreadHandle | None:
        return self._agent_registry.provider_thread_handle(self._agent_id)

    @property
    def persistent_thread(self) -> bool:
        return self._agent_registry.role_catalog.get(self.role).persistent_thread

    @property
    def supports_interactive_requests(self) -> bool:
        return self._agent_registry.role_catalog.get(self.role).supports_interactive_requests

    def get_handle(self) -> AgentHandle | None:
        return self._runtime_service.get_handle(self._agent_id)

    def snapshot(self, *, record: AgentRunRecord | None = None) -> OrchestratorAgentSnapshot:
        return build_agent_snapshot(
            agent_id=self._agent_id,
            instance=self.record,
            record=record or self.latest_run(),
            runtime_service=self._runtime_service,
            output_service=self._output_service,
        )

    def create_run_record(
        self,
        *,
        task_id: str,
        branch: str | None,
        worktree_path: str | None,
        prompt: str | None = None,
        skills: list[str] | None = None,
        retry_count: int = 0,
        max_retries: int = 3,
    ) -> AgentRunRecord:
        return self._agent_registry.create_run_record(
            agent=self.record,
            task_id=task_id,
            branch=branch,
            worktree_path=worktree_path,
            prompt=prompt,
            skills=skills,
            retry_count=retry_count,
            max_retries=max_retries,
        )

    async def start_run(
        self,
        *,
        agent_record: AgentRunRecord,
        prompt: str,
        cwd: str,
        resume_thread_id: str | None = None,
        increment_spawn: bool = True,
    ) -> AgentHandle:
        return await self._runtime_service.start_run(
            agent_record=agent_record,
            prompt=prompt,
            cwd=cwd,
            resume_thread_id=resume_thread_id,
            increment_spawn=increment_spawn,
        )

    async def start_new_run(
        self,
        *,
        task_id: str,
        branch: str | None,
        worktree_path: str | None,
        prompt: str,
        cwd: str,
        skills: list[str] | None = None,
        retry_count: int = 0,
        max_retries: int = 3,
        resume_thread_id: str | None = None,
        increment_spawn: bool = True,
    ) -> StartedAgentRun:
        agent_record = self.create_run_record(
            task_id=task_id,
            branch=branch,
            worktree_path=worktree_path,
            prompt=prompt,
            skills=skills,
            retry_count=retry_count,
            max_retries=max_retries,
        )
        handle = await self.start_run(
            agent_record=agent_record,
            prompt=prompt,
            cwd=cwd,
            resume_thread_id=resume_thread_id,
            increment_spawn=increment_spawn,
        )
        return StartedAgentRun(agent=self, agent_record=agent_record, handle=handle)

    async def resume_run(
        self,
        *,
        agent_record: AgentRunRecord,
        prompt: str,
        cwd: str,
        provider_thread: ProviderThreadHandle | None = None,
    ) -> AgentHandle:
        resolved_provider_thread = provider_thread or self.provider_thread_handle()
        if resolved_provider_thread is None or not resolved_provider_thread.resumable:
            raise ValueError(f"Agent {self._agent_id} has no resumable provider thread")
        return await self._runtime_service.resume_run(
            agent_record=agent_record,
            prompt=prompt,
            cwd=cwd,
            provider_thread=resolved_provider_thread,
        )

    async def wait_for_run(self, *, release_terminal: bool = True) -> RuntimeExecutionResult:
        return await self._runtime_service.wait_for_run(
            agent_id=self._agent_id,
            release_terminal=release_terminal,
        )

    async def respond_to_request(
        self,
        request_id: int | str,
        *,
        result: object | None = None,
        error: dict[str, object] | None = None,
    ) -> OrchestratorAgentSnapshot:
        await self._runtime_service.respond_to_request(
            agent_id=self._agent_id,
            request_id=request_id,
            result=result,
            error=error,
        )
        return self.snapshot()

    async def interrupt(self) -> OrchestratorAgentSnapshot:
        await self._runtime_service.interrupt_run(agent_id=self._agent_id)
        return self.snapshot()

    async def kill(self) -> OrchestratorAgentSnapshot:
        await self._runtime_service.kill_run(agent_id=self._agent_id)
        return self.snapshot()


def build_agent_snapshot(
    *,
    agent_id: str,
    instance: AgentInstanceRecord,
    record: AgentRunRecord | None,
    runtime_service: AgentRuntimeService,
    output_service: AgentOutputProjectionService | None = None,
) -> OrchestratorAgentSnapshot:
    """Build the orchestrator-facing snapshot for one stable agent instance."""

    handle = runtime_service.get_handle(agent_id)
    output = output_service.output_for_agent(agent_id) if output_service is not None else None

    if record is not None and handle is not None:
        handle_snapshot = runtime_service.snapshot_handle(handle=handle, agent_record=record)
        runtime = AgentSnapshotRuntime(
            status=record.lifecycle.status.value,
            state=handle_snapshot.runtime.state,
            has_handle=True,
            active=True,
            done=handle_snapshot.runtime.done,
            awaiting_input=handle_snapshot.runtime.awaiting_input,
            pid=record.lifecycle.pid,
            started_at=record.lifecycle.started_at,
            finished_at=record.lifecycle.finished_at,
            input_requests=list(handle_snapshot.runtime.input_requests),
        )
        provider = AgentSnapshotProvider(
            thread_id=handle_snapshot.provider.thread_id,
            thread_path=handle_snapshot.provider.thread_path,
            resume_cursor=handle_snapshot.provider.resume_cursor,
            native_event_log=record.provider.native_event_log,
            canonical_event_log=record.provider.canonical_event_log,
        )
    elif record is not None:
        done = record.lifecycle.status in AgentRunRecord.TERMINAL_STATUSES
        provider_thread = ProviderResumeHandle.from_provider_metadata(record.provider) or ProviderResumeHandle(
            kind=record.provider.kind
        )
        runtime = AgentSnapshotRuntime(
            status=record.lifecycle.status.value,
            state=record.lifecycle.status.value,
            has_handle=False,
            active=not done,
            done=done,
            awaiting_input=record.lifecycle.status.value == "awaiting_input",
            pid=record.lifecycle.pid,
            started_at=record.lifecycle.started_at,
            finished_at=record.lifecycle.finished_at,
        )
        provider = AgentSnapshotProvider(
            thread_id=provider_thread.thread_id,
            thread_path=provider_thread.thread_path,
            resume_cursor=provider_thread.resume_cursor,
            native_event_log=record.provider.native_event_log,
            canonical_event_log=record.provider.canonical_event_log,
        )
    else:
        runtime = AgentSnapshotRuntime(
            status="idle",
            state="idle",
            has_handle=False,
            active=False,
            done=False,
            awaiting_input=False,
        )
        provider = AgentSnapshotProvider()

    task_id = record.identity.task_id if record is not None else (instance.scope.scope_id if instance.scope.scope_type == "task" else None)
    role = record.identity.role if record is not None else instance.identity.role
    branch = record.context.branch if record is not None else None
    worktree_path = record.context.worktree_path if record is not None else None
    summary = record.outcome.summary if record is not None else None
    error = record.outcome.error if record is not None else None
    run_id = record.identity.run_id if record is not None else None

    return OrchestratorAgentSnapshot(
        identity=AgentSnapshotIdentity(
            agent_id=agent_id,
            run_id=run_id,
            task_id=task_id,
            role=role,
            scope_type=instance.scope.scope_type,
            scope_id=instance.scope.scope_id,
        ),
        runtime=runtime,
        workspace=AgentSnapshotWorkspace(
            branch=branch,
            worktree_path=worktree_path,
        ),
        outcome=AgentSnapshotOutcome(
            summary=summary,
            error=error,
            output=output,
        ),
        provider=provider,
    )
