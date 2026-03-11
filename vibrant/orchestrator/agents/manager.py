"""Unified agent management service."""

from __future__ import annotations

from typing import Any

from vibrant.agents.runtime import AgentHandle, InputRequest, ProviderThreadHandle
from vibrant.models.agent import ProviderResumeHandle
from vibrant.models.agent import AgentRecord, AgentType
from vibrant.models.task import TaskInfo
from vibrant.orchestrator.execution.git_manager import GitWorktreeInfo

from ..agent_output import AgentOutputProjectionService
from ..types import (
    AgentOutput,
    AgentSnapshotIdentity,
    AgentSnapshotOutcome,
    AgentSnapshotProvider,
    AgentSnapshotRuntime,
    AgentSnapshotWorkspace,
    OrchestratorAgentSnapshot,
    RuntimeExecutionResult,
    TaskResult,
)
from .registry import AgentRegistry
from .runtime import AgentRuntimeService, RuntimeHandleSnapshot
from ..execution.service import TaskExecutionAttempt, TaskExecutionService

ManagedAgentSnapshot = OrchestratorAgentSnapshot


class AgentManagementService:
    """Public orchestrator facade for agent-related operations.

    This is the service boundary higher-level orchestrator code should depend on.
    Internally it delegates to focused services for persistence, live runtime
    handles, and task execution, but callers can treat it as the single entry
    point for agent management.
    """

    def __init__(
        self,
        *,
        agent_registry: AgentRegistry,
        runtime_service: AgentRuntimeService,
        execution_service: TaskExecutionService,
        output_service: AgentOutputProjectionService | None = None,
    ) -> None:
        self._agent_registry = agent_registry
        self._runtime_service = runtime_service
        self._execution_service = execution_service
        self._output_service = output_service

    @property
    def agent_registry(self) -> AgentRegistry:
        """Internal persistence service, exposed for compatibility."""
        return self._agent_registry

    @property
    def runtime_service(self) -> AgentRuntimeService:
        """Internal live-runtime service, exposed for compatibility."""
        return self._runtime_service

    @property
    def execution_service(self) -> TaskExecutionService:
        """Internal execution service, exposed for compatibility."""
        return self._execution_service

    def _coerce_agent_type(self, agent_type: AgentType | str | None) -> AgentType | None:
        if agent_type is None or isinstance(agent_type, AgentType):
            return agent_type
        if isinstance(agent_type, str):
            try:
                return AgentType(agent_type.strip().lower())
            except ValueError as exc:
                raise ValueError(f"Unsupported agent type filter: {agent_type!r}") from exc
        raise TypeError(
            f"agent_type filter must be AgentType, str, or None; got {type(agent_type).__name__}"
        )

    # ------------------------------------------------------------------
    # Durable record / snapshot access
    # ------------------------------------------------------------------

    def get_record(self, agent_id: str) -> AgentRecord | None:
        """Return the persisted record for one agent, if known."""
        return self._agent_registry.get(agent_id)

    def list_records(self) -> list[AgentRecord]:
        """Return all persisted records."""
        return self._agent_registry.list_records()

    def records_for_task(self, task_id: str) -> list[AgentRecord]:
        """Return persisted records for one task."""
        return self._agent_registry.records_for_task(task_id)

    def provider_thread_handle(self, agent_id: str) -> ProviderThreadHandle | None:
        """Return the durable provider-thread handle for an agent."""
        return self._agent_registry.provider_thread_handle(agent_id)

    def snapshot_for_record(self, record: AgentRecord) -> ManagedAgentSnapshot:
        """Return a unified snapshot for a persisted record."""
        handle = self._runtime_service.get_handle(record.identity.agent_id)
        done = record.lifecycle.status in AgentRecord.TERMINAL_STATUSES
        if handle is not None:
            handle_snapshot = self._runtime_service.snapshot_handle(handle=handle, agent_record=record)
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
        else:
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

        output = self._output_service.output_for_record(record) if self._output_service is not None else None

        return ManagedAgentSnapshot(
            identity=AgentSnapshotIdentity(
                agent_id=record.identity.agent_id,
                task_id=record.identity.task_id,
                agent_type=record.identity.type.value,
            ),
            runtime=runtime,
            workspace=AgentSnapshotWorkspace(
                branch=record.context.branch,
                worktree_path=record.context.worktree_path,
            ),
            outcome=AgentSnapshotOutcome(
                summary=record.outcome.summary,
                error=record.outcome.error,
                output=output,
            ),
            provider=provider,
        )

    def get_agent(self, agent_id: str) -> ManagedAgentSnapshot | None:
        """Return the unified snapshot for one agent, if known."""
        record = self._agent_registry.get(agent_id)
        if record is None:
            return None
        return self.snapshot_for_record(record)

    def list_agents(
        self,
        *,
        task_id: str | None = None,
        agent_type: AgentType | str | None = None,
        include_completed: bool = True,
        active_only: bool = False,
    ) -> list[ManagedAgentSnapshot]:
        """List agents with optional task/type/activity filters."""
        resolved_type = self._coerce_agent_type(agent_type)
        records = self._agent_registry.list_records()
        if task_id is not None:
            records = [record for record in records if record.identity.task_id == task_id]
        if resolved_type is not None:
            records = [record for record in records if record.identity.type is resolved_type]

        snapshots = [self.snapshot_for_record(record) for record in records]
        if active_only:
            return [snapshot for snapshot in snapshots if snapshot.runtime.active]
        if not include_completed:
            return [
                snapshot
                for snapshot in snapshots
                if not snapshot.runtime.done or snapshot.runtime.awaiting_input
            ]
        return snapshots

    def list_active_agents(self) -> list[ManagedAgentSnapshot]:
        """Return actively managed in-flight agent runs."""
        return self.list_agents(active_only=True)

    def latest_for_task(
        self,
        task_id: str,
        *,
        agent_type: AgentType | str | None = None,
    ) -> ManagedAgentSnapshot | None:
        """Return the latest unified snapshot for a task."""
        record = self._agent_registry.latest_for_task(
            task_id,
            agent_type=self._coerce_agent_type(agent_type),
        )
        if record is None:
            return None
        return self.snapshot_for_record(record)

    # ------------------------------------------------------------------
    # Record construction helpers
    # ------------------------------------------------------------------

    def create_code_agent_record(self, *, task: TaskInfo, worktree: GitWorktreeInfo, prompt: str) -> AgentRecord:
        """Build a code-agent record for one task run."""
        return self._agent_registry.create_code_agent_record(task=task, worktree=worktree, prompt=prompt)

    def create_task_agent_record(
        self,
        *,
        agent_type: AgentType,
        task_id: str,
        branch: str | None,
        worktree_path: str | None,
        prompt: str | None = None,
        skills: list[str] | None = None,
        retry_count: int = 0,
        max_retries: int = 3,
        runtime_mode: str | None = None,
    ) -> AgentRecord:
        """Build a task-scoped record for any supported agent kind."""
        return self._agent_registry.create_task_agent_record(
            agent_type=agent_type,
            task_id=task_id,
            branch=branch,
            worktree_path=worktree_path,
            prompt=prompt,
            skills=skills,
            retry_count=retry_count,
            max_retries=max_retries,
            runtime_mode=runtime_mode,
        )

    def create_merge_agent_record(self, *, task_id: str, branch: str, worktree_path: str) -> AgentRecord:
        """Build a merge-agent record."""
        return self._agent_registry.create_merge_agent_record(
            task_id=task_id,
            branch=branch,
            worktree_path=worktree_path,
        )

    def create_test_agent_record(
        self,
        *,
        task_id: str,
        branch: str | None,
        worktree_path: str,
        prompt: str | None = None,
    ) -> AgentRecord:
        """Build a validation/test-agent record."""
        return self._agent_registry.create_test_agent_record(
            task_id=task_id,
            branch=branch,
            worktree_path=worktree_path,
            prompt=prompt,
        )

    # ------------------------------------------------------------------
    # Live handle inspection and control
    # ------------------------------------------------------------------

    @property
    def supports_handles(self) -> bool:
        """Whether the runtime supports durable handle APIs."""
        return self._runtime_service.supports_handles

    def get_handle(self, agent_id: str) -> AgentHandle | None:
        """Return the tracked live handle for an agent."""
        return self._runtime_service.get_handle(agent_id)

    def release_handle(self, agent_id: str) -> AgentHandle | None:
        """Stop tracking a live handle."""
        return self._runtime_service.release_handle(agent_id)

    def snapshot_handle(
        self,
        *,
        agent_id: str | None = None,
        handle: AgentHandle | None = None,
        agent_record: AgentRecord | None = None,
    ) -> RuntimeHandleSnapshot:
        """Return a serializable snapshot for one tracked handle."""
        return self._runtime_service.snapshot_handle(
            agent_id=agent_id,
            handle=handle,
            agent_record=agent_record,
        )

    def list_handle_snapshots(self, *, include_completed: bool = True) -> list[RuntimeHandleSnapshot]:
        """List tracked runtime handles."""
        return self._runtime_service.list_handle_snapshots(include_completed=include_completed)

    async def start_run(
        self,
        *,
        agent_record: AgentRecord,
        prompt: str,
        cwd: str,
        resume_thread_id: str | None = None,
        increment_spawn: bool = True,
    ) -> AgentHandle:
        """Start an agent run and return its live handle."""
        return await self._runtime_service.start_run(
            agent_record=agent_record,
            prompt=prompt,
            cwd=cwd,
            resume_thread_id=resume_thread_id,
            increment_spawn=increment_spawn,
        )

    async def resume_run(
        self,
        *,
        agent_record: AgentRecord,
        prompt: str,
        cwd: str,
        provider_thread: ProviderThreadHandle | None = None,
    ) -> AgentHandle:
        """Resume a run from durable provider-thread metadata."""
        return await self._runtime_service.resume_run(
            agent_record=agent_record,
            prompt=prompt,
            cwd=cwd,
            provider_thread=provider_thread,
        )

    async def start_task_run(
        self,
        *,
        worktree: GitWorktreeInfo,
        prompt: str,
        agent_record: AgentRecord,
        resume_thread_id: str | None = None,
    ) -> AgentHandle:
        """Start a worktree-scoped run and return its handle."""
        return await self._runtime_service.start_task(
            worktree=worktree,
            prompt=prompt,
            agent_record=agent_record,
            resume_thread_id=resume_thread_id,
        )

    async def resume_task_run(
        self,
        *,
        worktree: GitWorktreeInfo,
        prompt: str,
        agent_record: AgentRecord,
        provider_thread: ProviderThreadHandle | None = None,
    ) -> AgentHandle:
        """Resume a worktree-scoped run and return its handle."""
        return await self._runtime_service.resume_task(
            worktree=worktree,
            prompt=prompt,
            agent_record=agent_record,
            provider_thread=provider_thread,
        )

    async def wait_for_agent(
        self,
        agent_id: str,
        *,
        release_terminal: bool = True,
    ) -> RuntimeExecutionResult:
        """Wait for a live agent handle and return the normalized runtime result."""
        return await self._runtime_service.wait_for_run(
            agent_id=agent_id,
            release_terminal=release_terminal,
        )

    async def respond_to_request(
        self,
        agent_id: str,
        request_id: int | str,
        *,
        result: Any | None = None,
        error: dict[str, Any] | None = None,
    ) -> ManagedAgentSnapshot:
        """Answer a pending provider request for a live agent."""
        await self._runtime_service.respond_to_request(
            agent_id=agent_id,
            request_id=request_id,
            result=result,
            error=error,
        )
        snapshot = self.get_agent(agent_id)
        if snapshot is None:
            raise KeyError(f"Unknown agent record: {agent_id}")
        return snapshot

    async def interrupt_agent(self, agent_id: str) -> ManagedAgentSnapshot:
        """Interrupt a live agent turn and return the updated snapshot."""
        await self._runtime_service.interrupt_run(agent_id=agent_id)
        snapshot = self.get_agent(agent_id)
        if snapshot is None:
            raise KeyError(f"Unknown agent record: {agent_id}")
        return snapshot

    async def kill_agent(self, agent_id: str) -> ManagedAgentSnapshot:
        """Force-stop a live agent and return the updated snapshot."""
        await self._runtime_service.kill_run(agent_id=agent_id)
        snapshot = self.get_agent(agent_id)
        if snapshot is None:
            raise KeyError(f"Unknown agent record: {agent_id}")
        return snapshot

    # ------------------------------------------------------------------
    # Task-attempt orchestration
    # ------------------------------------------------------------------

    async def start_task(
        self,
        task: TaskInfo,
        *,
        resume_thread_id: str | None = None,
    ) -> TaskExecutionAttempt:
        """Start a task-scoped agent run without waiting for completion."""
        return await self._execution_service.start_task_attempt(task, resume_thread_id=resume_thread_id)

    async def wait_for_task(self, attempt: TaskExecutionAttempt) -> TaskResult:
        """Wait for a previously started task attempt through review/merge."""
        return await self._execution_service.wait_for_task_attempt(attempt)

    async def execute_next_task(self) -> TaskResult | None:
        """Execute the next queued task through the full agent pipeline."""
        return await self._execution_service.execute_next_task()

    async def execute_until_blocked(self) -> list[TaskResult]:
        """Run queued tasks until user input or workflow state blocks progress."""
        return await self._execution_service.execute_until_blocked()
