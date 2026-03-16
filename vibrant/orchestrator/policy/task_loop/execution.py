"""Task-loop execution runtime assembly."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vibrant.agents.code_agent import CodeAgent
from vibrant.agents.runtime import BaseAgentRuntime
from vibrant.config import DEFAULT_CONFIG_DIR, VibrantConfig
from vibrant.models.agent import AgentInstanceProviderConfig, AgentRunRecord, ProviderResumeHandle
from vibrant.models.task import TaskInfo
from vibrant.providers.invocation_compiler import compile_provider_invocation
from vibrant.providers.registry import provider_transport

from ...basic.conversation import ConversationStreamService
from ...basic.runtime import AgentRuntimeService
from ...basic.stores import AgentInstanceStore, AgentRunStore, AttemptStore
from ...basic.workspace import WorkspaceService
from ...types import (
    AttemptCompletion,
    AttemptExecutionSnapshot,
    AttemptExecutionView,
    AttemptRecord,
    AttemptRecoveryState,
    AttemptStatus,
    ProviderAdapterFactory,
    WorkspaceHandle,
)
from .models import PreparedTaskExecution, WORKER_INPUT_UNSUPPORTED_ERROR
from .roles import ensure_task_agent_instance
from .sessions import AttemptExecutionSessionResource
from ..shared.capabilities import worker_binding_preset

if TYPE_CHECKING:
    from ...basic.binding import AgentSessionBindingService
    from ...interface.mcp import OrchestratorFastMCPHost


@dataclass(slots=True)
class ExecutionCoordinator:
    """Run code-stage mechanics for one task attempt."""

    project_root: Path
    config: VibrantConfig
    attempt_store: AttemptStore
    agent_instance_store: AgentInstanceStore
    agent_run_store: AgentRunStore
    workspace_service: WorkspaceService
    runtime_service: AgentRuntimeService
    conversation_stream: ConversationStreamService
    adapter_factory: ProviderAdapterFactory
    binding_service: AgentSessionBindingService | None = None
    mcp_host: OrchestratorFastMCPHost | None = None
    execution_session: AttemptExecutionSessionResource = field(init=False, repr=False)
    _binding_ids_by_run_id: dict[str, str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._binding_ids_by_run_id = {}
        self.execution_session = AttemptExecutionSessionResource(
            attempt_store=self.attempt_store,
            run_store=self.agent_run_store,
            workspace_service=self.workspace_service,
            runtime_service=self.runtime_service,
            resume_callback=self._resume_attempt_live,
        )

    def attach_mcp_bridge(
        self,
        *,
        binding_service: AgentSessionBindingService,
        mcp_host: OrchestratorFastMCPHost,
    ) -> None:
        self.binding_service = binding_service
        self.mcp_host = mcp_host

    async def start_attempt(self, prepared: PreparedTaskExecution) -> AttemptRecord:
        lease = prepared.lease
        workspace = self.workspace_service.prepare_task_workspace(
            lease.task_id,
            branch_hint=lease.branch_hint,
        )
        attempt = self.attempt_store.create(
            task_id=lease.task_id,
            task_definition_version=lease.task_definition_version,
            workspace_id=workspace.workspace_id,
        )
        workspace = self.workspace_service.attach_attempt(
            workspace_id=workspace.workspace_id,
            attempt_id=attempt.attempt_id,
        )
        agent_record = self._build_run_record(
            task=prepared.task,
            workspace=workspace,
            prompt=prepared.prompt,
        )
        self._persist_run(agent_record)

        conversation_id = f"attempt-{attempt.attempt_id}"
        self.conversation_stream.bind_run(
            conversation_id=conversation_id,
            run_id=agent_record.identity.run_id,
        )
        self.conversation_stream.record_host_message(
            conversation_id=conversation_id,
            role="system",
            text=f"Starting attempt {attempt.attempt_id} for task {lease.task_id}.",
        )
        self.execution_session.bind_run(
            attempt.attempt_id,
            run_id=agent_record.identity.run_id,
            conversation_id=conversation_id,
            status=AttemptStatus.RUNNING,
        )
        try:
            await self._launch_code_runtime(
                agent_record=agent_record,
                prompt=prepared.prompt,
                workspace_path=workspace.path,
                provider_thread=None,
                conversation_id=conversation_id,
            )
        except Exception:
            self.execution_session.set_status(attempt.attempt_id, AttemptStatus.FAILED)
            raise
        persisted_attempt = self.attempt_store.get(attempt.attempt_id)
        if persisted_attempt is None:
            raise KeyError(f"Attempt not found after start: {attempt.attempt_id}")
        return persisted_attempt

    def attempt_execution(self, attempt_id: str) -> AttemptExecutionView | None:
        return self.execution_session.get_view(attempt_id)

    def list_active_attempt_executions(self) -> list[AttemptExecutionView]:
        return self.execution_session.list_active_views()

    def list_attempt_executions(
        self,
        *,
        task_id: str | None = None,
        status: AttemptStatus | None = None,
    ) -> list[AttemptExecutionView]:
        records = (
            self.attempt_store.list_by_task(task_id)
            if task_id is not None
            else self.attempt_store.list_all()
        )
        views: list[AttemptExecutionView] = []
        for record in records:
            view = self.execution_session.get_view(record.attempt_id)
            if view is None:
                continue
            if status is not None and view.status is not status:
                continue
            views.append(view)
        return views

    def attempt_recovery_state(self, attempt_id: str) -> AttemptRecoveryState | None:
        return self.execution_session.get_recovery_state(attempt_id)

    def list_active_attempt_recovery_states(self) -> list[AttemptRecoveryState]:
        return self.execution_session.list_active_recovery_states()

    def next_attempt_to_recover(self) -> AttemptRecoveryState | None:
        return self.execution_session.next_recoverable_state()

    def durable_attempt_completion(self, attempt_id: str) -> AttemptCompletion | None:
        return self.execution_session.durable_completion(attempt_id)

    async def recover_attempt(
        self,
        attempt_id: str,
        *,
        prepared: PreparedTaskExecution,
    ) -> AttemptRecord:
        await self.execution_session.resume(attempt_id, prepared=prepared)
        recovered = self.attempt_store.get(attempt_id)
        if recovered is None:
            raise KeyError(f"Attempt not found after resume: {attempt_id}")
        return recovered

    async def resume_attempt(
        self,
        attempt_id: str,
        *,
        prepared: PreparedTaskExecution,
    ) -> AttemptRecord:
        session = self.execution_session.get(attempt_id)
        if session is None:
            raise KeyError(f"Attempt not found: {attempt_id}")
        if session.live:
            attempt = self.attempt_store.get(attempt_id)
            if attempt is None:
                raise KeyError(f"Attempt not found: {attempt_id}")
            return attempt
        if not session.workspace_path:
            raise RuntimeError(f"Attempt is not resumable: {attempt_id}")
        return await self.recover_attempt(attempt_id, prepared=prepared)

    async def await_attempt_completion(self, attempt_id: str) -> AttemptCompletion:
        attempt = self.attempt_store.get(attempt_id)
        if attempt is None:
            raise KeyError(f"Attempt not found: {attempt_id}")
        if attempt.code_run_id is None:
            raise ValueError(f"Attempt has no code run: {attempt_id}")
        session = self.execution_session.get(attempt_id)
        incarnation_id = session.incarnation_id if session is not None else None

        wait_for_run = self.runtime_service.wait_for_run
        try:
            runtime_result = await wait_for_run(
                attempt.code_run_id,
                incarnation_id=incarnation_id,
            )
        except TypeError as exc:
            if "incarnation_id" not in str(exc):
                raise
            runtime_result = await wait_for_run(attempt.code_run_id)
        if runtime_result.awaiting_input:
            error = runtime_result.error or WORKER_INPUT_UNSUPPORTED_ERROR
            return AttemptCompletion(
                attempt_id=attempt.attempt_id,
                task_id=attempt.task_id,
                status="failed",
                code_run_id=attempt.code_run_id,
                workspace_ref=attempt.workspace_id,
                diff_ref=None,
                validation=None,
                summary=runtime_result.summary,
                error=error,
                conversation_ref=attempt.conversation_id,
                provider_events_ref=runtime_result.provider_events_ref,
            )
        completion = AttemptCompletion(
            attempt_id=attempt.attempt_id,
            task_id=attempt.task_id,
            status="failed" if runtime_result.error else "succeeded",
            code_run_id=attempt.code_run_id,
            workspace_ref=attempt.workspace_id,
            diff_ref=None,
            validation=None,
            summary=runtime_result.summary,
            error=runtime_result.error,
            conversation_ref=attempt.conversation_id,
            provider_events_ref=runtime_result.provider_events_ref,
        )
        if completion.status == "succeeded":
            workspace = self.workspace_service.get_workspace(
                task_id=attempt.task_id,
                workspace_id=attempt.workspace_id,
            )
            self.workspace_service.capture_result_commit(workspace)
        return completion

    def reconcile_active_sessions(self) -> list[AttemptExecutionSnapshot]:
        return self.execution_session.reconcile_active()

    async def pause_active_attempts(self) -> list[AttemptExecutionSnapshot]:
        paused: list[AttemptExecutionSnapshot] = []
        for session in self.execution_session.list_active():
            if not session.live or session.run_id is None:
                continue
            self.runtime_service.annotate_run(session.run_id, stop_reason="paused")
            await self.runtime_service.kill_run(session.run_id)
            refreshed = self.execution_session.get(session.attempt_id)
            if refreshed is not None:
                paused.append(refreshed)
        return paused

    def _persist_run(self, run_record: AgentRunRecord) -> None:
        self.agent_run_store.upsert(run_record)
        instance = self.agent_instance_store.get(run_record.identity.agent_id)
        if instance is None:
            return
        instance.mark_run_updated(
            agent_id=run_record.identity.agent_id,
            run_id=run_record.identity.run_id,
            status=run_record.lifecycle.status,
        )
        self.agent_instance_store.upsert(instance)

    async def _launch_code_runtime(
        self,
        *,
        agent_record: AgentRunRecord,
        prompt: str,
        workspace_path: str,
        provider_thread: ProviderResumeHandle | None,
        conversation_id: str | None,
    ) -> None:
        runtime = BaseAgentRuntime(self._build_code_agent())
        binding_service, mcp_host = self._require_mcp_bridge()
        use_inprocess_mcp = bool(getattr(self.adapter_factory, "supports_inprocess_mcp", False))
        if not use_inprocess_mcp:
            await mcp_host.ensure_started()
        bound_capabilities = binding_service.bind_preset(
            preset=worker_binding_preset(
                binding_service.mcp_server,
                agent_record.identity.agent_id,
                agent_record.identity.role,
            ),
            run_id=agent_record.identity.run_id,
            conversation_id=conversation_id,
        )
        registered_binding = mcp_host.register_binding(bound_capabilities)
        self._binding_ids_by_run_id[agent_record.identity.run_id] = registered_binding.binding_id
        invocation_plan = compile_provider_invocation(
            agent_record.provider.kind,
            bound_capabilities.access,
        )
        invocation_plan.debug_metadata["mcp_asgi_app"] = mcp_host.http_app()
        if use_inprocess_mcp:
            mcp_access = invocation_plan.debug_metadata.get("mcp_access")
            if isinstance(mcp_access, dict) and not mcp_access.get("endpoint_url"):
                mcp_access["endpoint_url"] = "http://127.0.0.1/mcp"

        handle = None
        if provider_thread is not None and provider_thread.resumable:
            try:
                handle = await self.runtime_service.resume_run(
                    agent_record=agent_record,
                    prompt=prompt,
                    provider_thread=provider_thread,
                    cwd=workspace_path,
                    runtime=runtime,
                    on_record_updated=self._persist_run,
                    invocation_plan=invocation_plan,
                )
            except Exception:
                self._release_binding(agent_record.identity.run_id)
                raise
        else:
            try:
                handle = await self.runtime_service.start_run(
                    agent_record=agent_record,
                    prompt=prompt,
                    cwd=workspace_path,
                    runtime=runtime,
                    on_record_updated=self._persist_run,
                    invocation_plan=invocation_plan,
                )
            except Exception:
                self._release_binding(agent_record.identity.run_id)
                raise

        asyncio.create_task(
            self._monitor_handle(agent_record.identity.run_id, handle),
            name=f"attempt-mcp-binding-{agent_record.identity.run_id}",
        )

    async def _resume_attempt_live(
        self,
        attempt_id: str,
        session: AttemptExecutionSnapshot,
        resume_handle: ProviderResumeHandle | None,
        prepared: PreparedTaskExecution,
    ) -> AttemptExecutionSnapshot:
        attempt = self.attempt_store.get(attempt_id)
        if attempt is None:
            raise KeyError(f"Attempt not found: {attempt_id}")
        workspace = self.workspace_service.get_workspace(
            task_id=attempt.task_id,
            workspace_id=attempt.workspace_id,
        )
        existing_run_record = (
            self.agent_run_store.get(session.run_id)
            if session.run_id is not None
            else None
        )
        prompt = (
            existing_run_record.context.prompt_used
            if existing_run_record is not None and existing_run_record.context.prompt_used
            else prepared.prompt
        )
        agent_record = self._build_run_record(
            task=prepared.task,
            workspace=workspace,
            prompt=prompt,
            run_id=session.run_id or attempt.code_run_id,
        )
        self._persist_run(agent_record)

        conversation_id = attempt.conversation_id or f"attempt-{attempt.attempt_id}"
        self.conversation_stream.bind_run(
            conversation_id=conversation_id,
            run_id=agent_record.identity.run_id,
        )
        await self._launch_code_runtime(
            agent_record=agent_record,
            prompt=prompt,
            workspace_path=workspace.path,
            provider_thread=resume_handle,
            conversation_id=conversation_id,
        )
        return replace(
            session,
            run_id=agent_record.identity.run_id,
            incarnation_id=agent_record.identity.incarnation_id,
            conversation_id=conversation_id,
            status=AttemptStatus.RUNNING,
            live=False,
            awaiting_input=False,
            input_requests=[],
            run_stop_reason=None,
            provider_resume_handle=None,
            provider_thread_id=None,
            resumable=False,
            run_status=None,
        )

    def _build_code_agent(self) -> CodeAgent:
        return CodeAgent(
            self.project_root,
            self.config,
            adapter_factory=self.adapter_factory,
            on_agent_record_updated=self._persist_run,
        )

    def _build_run_record(
        self,
        *,
        task: TaskInfo,
        workspace: WorkspaceHandle,
        prompt: str,
        run_id: str | None = None,
    ) -> AgentRunRecord:
        instance = ensure_task_agent_instance(
            self.agent_instance_store,
            task=task,
            provider=self._task_agent_provider_config(),
        )
        return self._build_code_agent().build_run_record(
            task=task,
            worktree=workspace,
            prompt=prompt,
            agent_id=instance.identity.agent_id,
            role=instance.identity.role,
            run_id=run_id,
            vibrant_dir=self.project_root / DEFAULT_CONFIG_DIR,
        )

    def _task_agent_provider_config(self) -> AgentInstanceProviderConfig:
        return AgentInstanceProviderConfig(
            kind=self.config.provider_kind.value,
            transport=provider_transport(self.config.provider_kind),
            runtime_mode=self.config.sandbox_mode,
        )

    async def _monitor_handle(self, run_id: str, handle: Any) -> None:
        try:
            await handle.wait()
        finally:
            self._release_binding(run_id)

    def _release_binding(self, run_id: str | None) -> None:
        if run_id is None:
            return
        binding_id = self._binding_ids_by_run_id.pop(run_id, None)
        if binding_id is not None and self.mcp_host is not None:
            self.mcp_host.unregister_binding(binding_id)

    def _require_mcp_bridge(self) -> tuple[AgentSessionBindingService, OrchestratorFastMCPHost]:
        if self.binding_service is None or self.mcp_host is None:
            raise RuntimeError("ExecutionCoordinator requires MCP binding services before starting worker runs")
        return self.binding_service, self.mcp_host
