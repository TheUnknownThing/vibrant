"""Task-loop execution runtime assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vibrant.agents.code_agent import CodeAgent
from vibrant.agents.runtime import BaseAgentRuntime
from vibrant.config import DEFAULT_CONFIG_DIR
from vibrant.models.agent import AgentInstanceProviderConfig
from vibrant.providers.registry import provider_transport

from ...types import AttemptCompletion, AttemptRecord, AttemptStatus
from .models import PreparedTaskExecution
from .roles import ensure_task_agent_instance


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
        self.conversation_stream.bind_agent(
            conversation_id=conversation_id,
            agent_id=instance.identity.agent_id,
            run_id=agent_record.identity.run_id,
            task_id=lease.task_id,
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
            await self.runtime_service.start_run(
                agent_record=agent_record,
                prompt=prepared.prompt,
                cwd=workspace.path,
                runtime=BaseAgentRuntime(code_agent),
                on_record_updated=self._persist_run,
            )
        except Exception:
            self.attempt_store.update(attempt.attempt_id, status=AttemptStatus.FAILED)
            raise
        return self.attempt_store.get(attempt.attempt_id)

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
            provider_events_ref=runtime_result.agent_record.provider.canonical_event_log,
        )
        return completion

    def _persist_run(self, run_record) -> None:
        self.agent_run_store.upsert(run_record)
        instance = self.agent_instance_store.get(run_record.identity.agent_id)
        if instance is None:
            return
        instance.mark_run_updated(run_record)
        self.agent_instance_store.upsert(instance)
