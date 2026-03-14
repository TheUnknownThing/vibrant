"""Execution coordinator for task attempts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vibrant.agents.code_agent import CodeAgent
from vibrant.agents.runtime import BaseAgentRuntime

from ..types import AttemptCompletion, AttemptRecord, AttemptStatus, DispatchLease, ValidationOutcome, utc_now


@dataclass(slots=True)
class ExecutionCoordinator:
    """Mechanically run worker stages for one task attempt."""

    project_root: Any
    config: Any
    consensus_store: Any
    roadmap_store: Any
    attempt_store: Any
    agent_store: Any
    workspace_service: Any
    runtime_service: Any
    conversation_stream: Any
    workflow_policy: Any
    adapter_factory: Any

    async def start_attempt(self, lease: DispatchLease) -> AttemptRecord:
        task = self.roadmap_store.get_task(lease.task_id)
        if task is None:
            raise KeyError(f"Task not found: {lease.task_id}")

        workspace = self.workspace_service.prepare_task_workspace(
            lease.task_id,
            branch_hint=lease.branch_hint,
        )
        attempt = self.attempt_store.create(
            task_id=lease.task_id,
            task_definition_version=lease.task_definition_version,
            workspace_id=workspace.workspace_id,
        )

        prompt = self.roadmap_store.build_task_prompt(
            task_id=lease.task_id,
            consensus=self.consensus_store.load(),
        )
        code_agent = CodeAgent(
            self.project_root,
            self.config,
            adapter_factory=self.adapter_factory,
            on_agent_record_updated=self.agent_store.upsert,
        )
        agent_record = code_agent.build_agent_record(
            task=task,
            worktree=workspace,
            prompt=prompt,
            vibrant_dir=self.consensus_store.root,
        )
        self.agent_store.upsert(agent_record)

        conversation_id = f"attempt-{attempt.attempt_id}"
        self.conversation_stream.bind_agent(
            conversation_id=conversation_id,
            agent_id=agent_record.identity.agent_id,
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
            code_agent_id=agent_record.identity.agent_id,
            conversation_id=conversation_id,
        )
        self.workflow_policy.on_attempt_started(self.attempt_store.get(attempt.attempt_id))
        await self.runtime_service.start_run(
            agent_record=agent_record,
            prompt=prompt,
            cwd=workspace.path,
            runtime=BaseAgentRuntime(code_agent),
            on_record_updated=self.agent_store.upsert,
        )
        return self.attempt_store.get(attempt.attempt_id)

    async def await_attempt_completion(self, attempt_id: str) -> AttemptCompletion:
        attempt = self.attempt_store.get(attempt_id)
        if attempt is None:
            raise KeyError(f"Attempt not found: {attempt_id}")
        if attempt.code_agent_id is None:
            raise ValueError(f"Attempt has no code agent: {attempt_id}")

        runtime_result = await self.runtime_service.wait_for_run(attempt.code_agent_id)
        workspace = self.workspace_service.get_workspace(task_id=attempt.task_id, workspace_id=attempt.workspace_id)
        diff = self.workspace_service.collect_review_diff(workspace)
        validation = ValidationOutcome(
            status="skipped",
            agent_ids=[],
            summary="Validation not configured yet.",
        )
        completion = AttemptCompletion(
            attempt_id=attempt.attempt_id,
            task_id=attempt.task_id,
            status="awaiting_input" if runtime_result.awaiting_input else ("failed" if runtime_result.error else "succeeded"),
            code_agent_id=attempt.code_agent_id,
            workspace_ref=attempt.workspace_id,
            diff_ref=getattr(diff, "path", None),
            validation=validation,
            summary=runtime_result.summary,
            error=runtime_result.error,
            conversation_ref=attempt.conversation_id,
            provider_events_ref=runtime_result.agent_record.provider.canonical_event_log,
        )
        next_status = (
            AttemptStatus.AWAITING_INPUT
            if completion.status == "awaiting_input"
            else AttemptStatus.REVIEW_PENDING
            if completion.status == "succeeded"
            else AttemptStatus.CANCELLED
        )
        self.attempt_store.update(attempt.attempt_id, status=next_status)
        self.workflow_policy.on_attempt_completed(completion)
        return completion
