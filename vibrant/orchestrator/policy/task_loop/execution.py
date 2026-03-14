"""Task-loop execution runtime assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vibrant.agents.code_agent import CodeAgent
from vibrant.agents.runtime import BaseAgentRuntime
from vibrant.config import DEFAULT_CONFIG_DIR
from vibrant.models.agent import AgentInstanceProviderConfig, ProviderResumeHandle
from vibrant.providers.registry import provider_transport

from ...types import AttemptCompletion, AttemptExecutionView, AttemptRecord, AttemptRecoveryState, AttemptStatus
from .models import PreparedTaskExecution
from .roles import ensure_task_agent_instance
from .sessions import attempt_needs_recovery, project_attempt_execution_view, project_attempt_recovery_state


@dataclass(slots=True)
class ExecutionCoordinator:
    """Run code-stage mechanics for one task attempt."""

    project_root: Any
    config: Any
    attempt_store: Any
    agent_instance_store: Any
    agent_run_store: Any
    workspace_service: Any
    runtime_service: Any
    conversation_stream: Any
    adapter_factory: Any

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
        code_agent = CodeAgent(
            self.project_root,
            self.config,
            adapter_factory=self.adapter_factory,
            on_agent_record_updated=self._persist_run,
        )
        instance = ensure_task_agent_instance(
            self.agent_instance_store,
            task=prepared.task,
            provider=AgentInstanceProviderConfig(
                kind=self.config.provider_kind.value,
                transport=provider_transport(self.config.provider_kind),
                runtime_mode=self.config.sandbox_mode,
            ),
        )
        agent_record = code_agent.build_run_record(
            task=prepared.task,
            worktree=workspace,
            prompt=prepared.prompt,
            agent_id=instance.identity.agent_id,
            role=instance.identity.role,
            vibrant_dir=self.project_root / DEFAULT_CONFIG_DIR,
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
        self.attempt_store.update(
            attempt.attempt_id,
            status=AttemptStatus.RUNNING,
            code_run_id=agent_record.identity.run_id,
            conversation_id=conversation_id,
        )
        try:
            await self._launch_code_runtime(
                agent_record=agent_record,
                prompt=prepared.prompt,
                workspace_path=workspace.path,
                provider_thread=None,
            )
        except Exception:
            self.attempt_store.update(attempt.attempt_id, status=AttemptStatus.FAILED)
            raise
        return self.attempt_store.get(attempt.attempt_id)

    def attempt_execution(self, attempt_id: str) -> AttemptExecutionView | None:
        attempt = self.attempt_store.get(attempt_id)
        if attempt is None:
            return None
        return project_attempt_execution_view(
            attempt,
            run_store=self.agent_run_store,
            workspace_service=self.workspace_service,
            runtime_service=self.runtime_service,
        )

    def list_active_attempt_executions(self) -> list[AttemptExecutionView]:
        return [
            project_attempt_execution_view(
                attempt,
                run_store=self.agent_run_store,
                workspace_service=self.workspace_service,
                runtime_service=self.runtime_service,
            )
            for attempt in self.attempt_store.list_active()
        ]

    def attempt_recovery_state(self, attempt_id: str) -> AttemptRecoveryState | None:
        attempt = self.attempt_store.get(attempt_id)
        if attempt is None:
            return None
        return project_attempt_recovery_state(
            attempt,
            run_store=self.agent_run_store,
            workspace_service=self.workspace_service,
            runtime_service=self.runtime_service,
        )

    def list_active_attempt_recovery_states(self) -> list[AttemptRecoveryState]:
        return [
            project_attempt_recovery_state(
                attempt,
                run_store=self.agent_run_store,
                workspace_service=self.workspace_service,
                runtime_service=self.runtime_service,
            )
            for attempt in self.attempt_store.list_active()
        ]

    def next_attempt_to_recover(self) -> AttemptRecoveryState | None:
        for snapshot in self.list_active_attempt_recovery_states():
            if attempt_needs_recovery(snapshot):
                return snapshot
        return None

    async def recover_attempt(
        self,
        attempt_id: str,
        *,
        prepared: PreparedTaskExecution,
    ) -> AttemptRecord:
        attempt = self.attempt_store.get(attempt_id)
        if attempt is None:
            raise KeyError(f"Attempt not found: {attempt_id}")
        session = self.attempt_execution(attempt_id)
        if session is None:
            raise KeyError(f"Attempt session not found: {attempt_id}")
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
        code_agent = CodeAgent(
            self.project_root,
            self.config,
            adapter_factory=self.adapter_factory,
            on_agent_record_updated=self._persist_run,
        )
        instance = ensure_task_agent_instance(
            self.agent_instance_store,
            task=prepared.task,
            provider=AgentInstanceProviderConfig(
                kind=self.config.provider_kind.value,
                transport=provider_transport(self.config.provider_kind),
                runtime_mode=self.config.sandbox_mode,
            ),
        )
        agent_record = code_agent.build_run_record(
            task=prepared.task,
            worktree=workspace,
            prompt=prompt,
            agent_id=instance.identity.agent_id,
            role=instance.identity.role,
            vibrant_dir=self.project_root / DEFAULT_CONFIG_DIR,
        )
        self._persist_run(agent_record)

        conversation_id = attempt.conversation_id or f"attempt-{attempt.attempt_id}"
        self.conversation_stream.bind_run(
            conversation_id=conversation_id,
            run_id=agent_record.identity.run_id,
        )
        updated_attempt = self.attempt_store.update(
            attempt_id,
            status=AttemptStatus.RUNNING,
            code_run_id=agent_record.identity.run_id,
            conversation_id=conversation_id,
        )
        resume_handle = (
            self.agent_run_store.resume_handle_for_run(session.run_id)
            if session.run_id is not None
            else None
        )
        try:
            await self._launch_code_runtime(
                agent_record=agent_record,
                prompt=prompt,
                workspace_path=workspace.path,
                provider_thread=resume_handle,
            )
        except Exception:
            self.attempt_store.update(updated_attempt.attempt_id, status=AttemptStatus.FAILED)
            raise
        return self.attempt_store.get(updated_attempt.attempt_id)

    async def await_attempt_completion(self, attempt_id: str) -> AttemptCompletion:
        attempt = self.attempt_store.get(attempt_id)
        if attempt is None:
            raise KeyError(f"Attempt not found: {attempt_id}")
        if attempt.code_run_id is None:
            raise ValueError(f"Attempt has no code run: {attempt_id}")

        runtime_result = await self.runtime_service.wait_for_run(attempt.code_run_id)
        completion = AttemptCompletion(
            attempt_id=attempt.attempt_id,
            task_id=attempt.task_id,
            status="awaiting_input" if runtime_result.awaiting_input else ("failed" if runtime_result.error else "succeeded"),
            code_run_id=attempt.code_run_id,
            workspace_ref=attempt.workspace_id,
            diff_ref=None,
            validation=None,
            summary=runtime_result.summary,
            error=runtime_result.error,
            conversation_ref=attempt.conversation_id,
            provider_events_ref=runtime_result.provider_events_ref,
        )
        return completion

    def _persist_run(self, run_record) -> None:
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
        agent_record,
        prompt: str,
        workspace_path: str,
        provider_thread: ProviderResumeHandle | None,
    ) -> None:
        runtime = BaseAgentRuntime(
            CodeAgent(
                self.project_root,
                self.config,
                adapter_factory=self.adapter_factory,
                on_agent_record_updated=self._persist_run,
            )
        )
        if provider_thread is not None and provider_thread.resumable:
            await self.runtime_service.resume_run(
                agent_record=agent_record,
                prompt=prompt,
                provider_thread=provider_thread,
                cwd=workspace_path,
                runtime=runtime,
                on_record_updated=self._persist_run,
            )
            return
        await self.runtime_service.start_run(
            agent_record=agent_record,
            prompt=prompt,
            cwd=workspace_path,
            runtime=runtime,
            on_record_updated=self._persist_run,
        )
