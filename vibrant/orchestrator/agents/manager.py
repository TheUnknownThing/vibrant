"""Unified agent management service."""

from __future__ import annotations

from typing import Any

from vibrant.agents.runtime import AgentHandle, ProviderThreadHandle
from vibrant.models.agent import AgentRunRecord
from vibrant.models.task import TaskInfo
from vibrant.orchestrator.execution.git_manager import GitWorktreeInfo

from ..tasks.execution import TaskExecutionAttempt, TaskExecutionService
from ..types import OrchestratorAgentSnapshot, RuntimeExecutionResult, TaskResult
from .catalog import build_builtin_role_catalog
from .instance import AgentInstance, ManagedAgentInstance, StartedAgentRun
from .output_projection import AgentOutputProjectionService
from .registry import AgentRegistry
from .runtime import AgentRuntimeService, RuntimeHandleSnapshot

ManagedAgentSnapshot = OrchestratorAgentSnapshot


class AgentManagementService:
    """Public orchestrator facade for agent-related operations."""

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
        self._role_catalog = build_builtin_role_catalog()

    @property
    def agent_registry(self) -> AgentRegistry:
        return self._agent_registry

    @property
    def runtime_service(self) -> AgentRuntimeService:
        return self._runtime_service

    @property
    def execution_service(self) -> TaskExecutionService:
        return self._execution_service

    def _normalize_role(self, role: str | None) -> str | None:
        if role is None:
            return None
        try:
            return self._role_catalog.get(role).role
        except ValueError as exc:
            raise ValueError(f"Unsupported agent role filter: {role!r}") from exc

    def _managed_instance_from_record(self, record) -> ManagedAgentInstance:
        return ManagedAgentInstance.from_record(
            record,
            agent_registry=self._agent_registry,
            runtime_service=self._runtime_service,
            output_service=self._output_service,
        )

    # ------------------------------------------------------------------
    # Durable record / instance / snapshot access
    # ------------------------------------------------------------------

    def get_record(self, agent_id: str) -> AgentRunRecord | None:
        """Return the latest persisted run record for one stable agent."""
        return self._agent_registry.get(agent_id)

    def get_run(self, run_id: str) -> AgentRunRecord | None:
        """Return one persisted run record by run id."""
        return self._agent_registry.get_run(run_id)

    def list_records(self) -> list[AgentRunRecord]:
        """Return all persisted run records."""
        return self._agent_registry.list_records()

    def list_run_records(self) -> list[AgentRunRecord]:
        """Return all persisted run records."""
        return self.list_records()

    def records_for_task(self, task_id: str) -> list[AgentRunRecord]:
        """Return persisted run records for one task."""
        return self._agent_registry.records_for_task(task_id)

    def run_records_for_task(self, task_id: str) -> list[AgentRunRecord]:
        """Return persisted run records for one task."""
        return self.records_for_task(task_id)

    def provider_thread_handle(self, agent_id: str) -> ProviderThreadHandle | None:
        """Return the durable provider-thread handle for a stable agent."""
        instance = self.get_instance(agent_id)
        if instance is None:
            return None
        return instance.provider_thread_handle()

    def resolve_instance(
        self,
        *,
        role: str,
        scope_type: str,
        scope_id: str | None,
        provider_kind: str | None = None,
        runtime_mode: str | None = None,
    ) -> AgentInstance:
        record = self._agent_registry.resolve_instance(
            role=role,
            scope_type=scope_type,
            scope_id=scope_id,
            provider_kind=provider_kind,
            runtime_mode=runtime_mode,
        )
        return self._managed_instance_from_record(record)

    def get_instance(self, agent_id: str) -> AgentInstance | None:
        """Return the managed stable agent instance, if known."""
        instance_record = self._agent_registry.get_instance(agent_id)
        if instance_record is None:
            latest_run = self._agent_registry.get(agent_id)
            if latest_run is None:
                return None
            instance_record = self._agent_registry.ensure_instance_for_run(latest_run)
        return self._managed_instance_from_record(instance_record)

    def list_instances(
        self,
        *,
        task_id: str | None = None,
        role: str | None = None,
    ) -> list[AgentInstance]:
        """List stable agent instances with optional task/role filters."""
        resolved_role = self._normalize_role(role)
        instances = [self._managed_instance_from_record(record) for record in self._agent_registry.list_instances()]
        if task_id is not None:
            instances = [
                instance
                for instance in instances
                if instance.scope_type == "task" and instance.scope_id == task_id
            ]
        if resolved_role is not None:
            instances = [instance for instance in instances if instance.role == resolved_role]
        return instances

    def snapshot_for_record(self, record: AgentRunRecord) -> ManagedAgentSnapshot:
        """Return an instance snapshot anchored on a specific run record."""
        instance = self.get_instance(record.identity.agent_id)
        if instance is None:
            raise KeyError(f"Unknown agent instance: {record.identity.agent_id}")
        return instance.snapshot(record=record)

    def get_agent(self, agent_id: str) -> ManagedAgentSnapshot | None:
        """Return the unified snapshot for one stable agent, if known."""
        instance = self.get_instance(agent_id)
        if instance is None:
            return None
        return instance.snapshot()

    def get_agent_instance(self, agent_id: str) -> ManagedAgentSnapshot | None:
        """Return the stable-agent snapshot for one instance."""
        return self.get_agent(agent_id)

    def list_agents(
        self,
        *,
        task_id: str | None = None,
        role: str | None = None,
        include_completed: bool = True,
        active_only: bool = False,
    ) -> list[ManagedAgentSnapshot]:
        """List stable agents with optional task/role/activity filters."""
        snapshots = [instance.snapshot() for instance in self.list_instances(task_id=task_id, role=role)]
        if active_only:
            return [snapshot for snapshot in snapshots if snapshot.runtime.active]
        if not include_completed:
            return [snapshot for snapshot in snapshots if not snapshot.runtime.done or snapshot.runtime.awaiting_input]
        return snapshots

    def list_agent_instances(
        self,
        *,
        task_id: str | None = None,
        role: str | None = None,
        include_completed: bool = True,
        active_only: bool = False,
    ) -> list[ManagedAgentSnapshot]:
        """Return stable-agent snapshots using explicit instance terminology."""
        return self.list_agents(
            task_id=task_id,
            role=role,
            include_completed=include_completed,
            active_only=active_only,
        )

    def list_active_agents(self) -> list[ManagedAgentSnapshot]:
        return self.list_agents(active_only=True)

    def latest_for_task(
        self,
        task_id: str,
        *,
        role: str | None = None,
    ) -> ManagedAgentSnapshot | None:
        """Return the stable-agent snapshot backed by the latest run for a task."""
        record = self._agent_registry.latest_for_task(
            task_id,
            role=self._normalize_role(role),
        )
        if record is None:
            return None
        return self.snapshot_for_record(record)

    # ------------------------------------------------------------------
    # Record construction helpers
    # ------------------------------------------------------------------

    def create_code_agent_record(self, *, task: TaskInfo, worktree: GitWorktreeInfo, prompt: str) -> AgentRunRecord:
        return self._agent_registry.create_code_agent_record(task=task, worktree=worktree, prompt=prompt)

    def create_task_agent_record(
        self,
        *,
        role: str = "code",
        task_id: str,
        branch: str | None,
        worktree_path: str | None,
        prompt: str | None = None,
        skills: list[str] | None = None,
        retry_count: int = 0,
        max_retries: int = 3,
        runtime_mode: str | None = None,
    ) -> AgentRunRecord:
        return self._agent_registry.create_task_agent_record(
            role=role,
            task_id=task_id,
            branch=branch,
            worktree_path=worktree_path,
            prompt=prompt,
            skills=skills,
            retry_count=retry_count,
            max_retries=max_retries,
            runtime_mode=runtime_mode,
        )

    def create_merge_agent_record(self, *, task_id: str, branch: str, worktree_path: str) -> AgentRunRecord:
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
    ) -> AgentRunRecord:
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
        return self._runtime_service.supports_handles

    def get_handle(self, agent_id: str) -> AgentHandle | None:
        return self._runtime_service.get_handle(agent_id)

    def release_handle(self, agent_id: str) -> AgentHandle | None:
        return self._runtime_service.release_handle(agent_id)

    def snapshot_handle(
        self,
        *,
        agent_id: str | None = None,
        handle: AgentHandle | None = None,
        agent_record: AgentRunRecord | None = None,
    ) -> RuntimeHandleSnapshot:
        return self._runtime_service.snapshot_handle(
            agent_id=agent_id,
            handle=handle,
            agent_record=agent_record,
        )

    def list_handle_snapshots(self, *, include_completed: bool = True) -> list[RuntimeHandleSnapshot]:
        return self._runtime_service.list_handle_snapshots(include_completed=include_completed)

    async def start_run(
        self,
        *,
        agent_record: AgentRunRecord,
        prompt: str,
        cwd: str,
        resume_thread_id: str | None = None,
        increment_spawn: bool = True,
    ) -> AgentHandle:
        instance = self.get_instance(agent_record.identity.agent_id)
        if instance is None:
            raise KeyError(f"Unknown agent instance: {agent_record.identity.agent_id}")
        return await instance.start_run(
            agent_record=agent_record,
            prompt=prompt,
            cwd=cwd,
            resume_thread_id=resume_thread_id,
            increment_spawn=increment_spawn,
        )

    async def resume_run(
        self,
        *,
        agent_record: AgentRunRecord,
        prompt: str,
        cwd: str,
        provider_thread: ProviderThreadHandle | None = None,
    ) -> AgentHandle:
        instance = self.get_instance(agent_record.identity.agent_id)
        if instance is None:
            raise KeyError(f"Unknown agent instance: {agent_record.identity.agent_id}")
        return await instance.resume_run(
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
        agent_record: AgentRunRecord,
        resume_thread_id: str | None = None,
    ) -> AgentHandle:
        return await self.start_run(
            agent_record=agent_record,
            prompt=prompt,
            cwd=str(worktree.path),
            resume_thread_id=resume_thread_id,
        )

    async def resume_task_run(
        self,
        *,
        worktree: GitWorktreeInfo,
        prompt: str,
        agent_record: AgentRunRecord,
        provider_thread: ProviderThreadHandle | None = None,
    ) -> AgentHandle:
        return await self.resume_run(
            agent_record=agent_record,
            prompt=prompt,
            cwd=str(worktree.path),
            provider_thread=provider_thread,
        )

    async def wait_for_agent(
        self,
        agent_id: str,
        *,
        release_terminal: bool = True,
    ) -> RuntimeExecutionResult:
        instance = self.get_instance(agent_id)
        if instance is None:
            raise KeyError(f"Unknown agent instance: {agent_id}")
        return await instance.wait_for_run(release_terminal=release_terminal)

    async def respond_to_request(
        self,
        agent_id: str,
        request_id: int | str,
        *,
        result: Any | None = None,
        error: dict[str, Any] | None = None,
    ) -> ManagedAgentSnapshot:
        instance = self.get_instance(agent_id)
        if instance is None:
            raise KeyError(f"Unknown agent instance: {agent_id}")
        return await instance.respond_to_request(request_id, result=result, error=error)

    async def interrupt_agent(self, agent_id: str) -> ManagedAgentSnapshot:
        instance = self.get_instance(agent_id)
        if instance is None:
            raise KeyError(f"Unknown agent instance: {agent_id}")
        return await instance.interrupt()

    async def kill_agent(self, agent_id: str) -> ManagedAgentSnapshot:
        instance = self.get_instance(agent_id)
        if instance is None:
            raise KeyError(f"Unknown agent instance: {agent_id}")
        return await instance.kill()

    # ------------------------------------------------------------------
    # Task-attempt orchestration
    # ------------------------------------------------------------------

    async def start_task(
        self,
        task: TaskInfo,
        *,
        resume_thread_id: str | None = None,
    ) -> TaskExecutionAttempt:
        return await self._execution_service.start_task_attempt(task, resume_thread_id=resume_thread_id)

    async def wait_for_task(self, attempt: TaskExecutionAttempt) -> TaskResult:
        return await self._execution_service.wait_for_task_attempt(attempt)

    async def execute_next_task(self) -> TaskResult | None:
        return await self._execution_service.execute_next_task()

    async def execute_until_blocked(self) -> list[TaskResult]:
        return await self._execution_service.execute_until_blocked()
