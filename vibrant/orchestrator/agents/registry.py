"""Agent registry service."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from vibrant.agents.runtime import ProviderThreadHandle
from vibrant.models.agent import (
    AgentInstanceProviderConfig,
    AgentInstanceRecord,
    AgentProviderMetadata,
    AgentRunRecord,
    AgentStatus,
)
from vibrant.models.task import TaskInfo
from vibrant.orchestrator.execution.git_manager import GitWorktreeInfo

from .catalog import build_builtin_provider_catalog, build_builtin_role_catalog
from .store import AgentInstanceStore, AgentRecordStore

AgentRecordCallback = Callable[[AgentRunRecord], Any]
"""Callback type matching ``vibrant.agents.runtime.AgentRecordCallback``."""


class AgentRegistry:
    """Own durable agent-instance metadata and run-record construction."""

    def __init__(
        self,
        *,
        agent_store: AgentRecordStore,
        instance_store: AgentInstanceStore | None = None,
        vibrant_dir: str | Path,
    ) -> None:
        self.agent_store = agent_store
        self.instance_store = instance_store or AgentInstanceStore(vibrant_dir=vibrant_dir)
        self.vibrant_dir = Path(vibrant_dir)
        self.role_catalog = build_builtin_role_catalog()
        self.provider_catalog = build_builtin_provider_catalog()
        self._reconcile_instances_from_runs()

    def upsert(
        self,
        record: AgentRunRecord,
        *,
        increment_spawn: bool = False,
        rebuild_state: bool = True,
    ) -> Path:
        instance = self.ensure_instance_for_run(record)
        path = self.agent_store.upsert(
            record,
            increment_spawn=increment_spawn,
            rebuild_state=False,
        )
        instance.mark_run_updated(record)
        self.instance_store.upsert(instance)
        if rebuild_state:
            self.agent_store.state_store.rebuild_derived_state()
        return path

    def make_record_callback(self, *, increment_spawn: bool = False) -> AgentRecordCallback:
        """Return a callback that persists every run-record mutation."""

        def _persist(record: AgentRunRecord) -> None:
            self.upsert(record, increment_spawn=increment_spawn)

        return _persist

    def get(self, agent_id: str) -> AgentRunRecord | None:
        """Look up the latest persisted run for a stable agent id."""
        return self.agent_store.latest_for_agent(agent_id)

    def get_run(self, run_id: str) -> AgentRunRecord | None:
        """Look up a persisted run record by run id."""
        return self.agent_store.get(run_id)

    def get_instance(self, agent_id: str) -> AgentInstanceRecord | None:
        """Look up a persisted stable agent instance by id."""
        return self.instance_store.get(agent_id)

    def list_records(self) -> list[AgentRunRecord]:
        """Return all known run records ordered by start time then run id."""
        return self.agent_store.list_records()

    def list_instances(self) -> list[AgentInstanceRecord]:
        """Return all known stable agent instances in id order."""
        return self.instance_store.list_records()

    def list_active_records(self) -> list[AgentRunRecord]:
        """Return non-terminal run records ordered by start time then run id."""
        return [record for record in self.list_records() if record.lifecycle.status not in AgentRunRecord.TERMINAL_STATUSES]

    def records_for_task(self, task_id: str) -> list[AgentRunRecord]:
        """Return run records for a task ordered by start time then run id."""
        return self.agent_store.records_for_task(task_id)

    def latest_for_task(
        self,
        task_id: str,
        *,
        role: str | None = None,
    ) -> AgentRunRecord | None:
        """Return the latest run for a task, optionally filtered by role."""
        return self.agent_store.latest_for_task(task_id, role=role)

    def provider_thread_handle(self, agent_id: str) -> ProviderThreadHandle | None:
        """Return the latest persisted provider-thread handle for a stable agent id."""
        return self.agent_store.provider_thread_handle(agent_id)

    def resolve_instance(
        self,
        *,
        role: str,
        scope_type: str,
        scope_id: str | None,
        provider_kind: str | None = None,
        runtime_mode: str | None = None,
    ) -> AgentInstanceRecord:
        role_spec = self.role_catalog.get(role)
        existing = self.instance_store.find(role=role_spec.role, scope_type=scope_type, scope_id=scope_id)
        if existing is not None:
            updated = False
            if provider_kind is not None and existing.provider.kind != provider_kind:
                self.provider_catalog.get(provider_kind)
                existing.provider.kind = provider_kind
                updated = True
            if runtime_mode is not None and existing.provider.runtime_mode != runtime_mode:
                existing.provider.runtime_mode = runtime_mode
                updated = True
            if updated:
                existing.updated_at = datetime.now(timezone.utc)
                self.instance_store.upsert(existing)
            return existing

        provider_spec = self.provider_catalog.get(provider_kind or role_spec.default_provider_kind)
        scope_key = _scope_key(scope_type, scope_id)
        agent_id = f"{role_spec.agent_id_prefix}-{scope_key}" if scope_key else role_spec.agent_id_prefix
        record = AgentInstanceRecord(
            identity={
                "agent_id": agent_id,
                "role": role_spec.role,
            },
            scope={
                "scope_type": scope_type,
                "scope_id": scope_id,
            },
            provider=AgentInstanceProviderConfig(
                kind=provider_spec.kind,
                transport=provider_spec.default_transport,
                runtime_mode=runtime_mode or role_spec.default_runtime_mode,
            ),
        )
        self.instance_store.upsert(record)
        return record

    def ensure_instance_for_run(self, record: AgentRunRecord) -> AgentInstanceRecord:
        existing = self.instance_store.get(record.identity.agent_id)
        if existing is not None:
            return existing

        scope_type = "task"
        scope_id: str | None = record.identity.task_id
        if record.identity.role == "gatekeeper":
            scope_type = "project"
            scope_id = "project"

        instance = AgentInstanceRecord(
            identity={
                "agent_id": record.identity.agent_id,
                "role": record.identity.role,
            },
            scope={
                "scope_type": scope_type,
                "scope_id": scope_id,
            },
            provider=AgentInstanceProviderConfig(
                kind=record.provider.kind,
                transport=record.provider.transport,
                runtime_mode=record.provider.runtime_mode,
            ),
        )
        self.instance_store.upsert(instance)
        return instance

    def create_code_agent_record(self, *, task: TaskInfo, worktree: GitWorktreeInfo, prompt: str) -> AgentRunRecord:
        return self.create_execution_agent_record(task=task, worktree=worktree, prompt=prompt)

    def create_execution_agent_record(self, *, task: TaskInfo, worktree: GitWorktreeInfo, prompt: str) -> AgentRunRecord:
        return self.create_task_agent_record(
            role=task.agent_role,
            task_id=task.id,
            branch=task.branch,
            worktree_path=str(worktree.path),
            prompt=prompt,
            skills=list(task.skills),
            retry_count=task.retry_count,
            max_retries=task.max_retries,
        )

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
        provider_kind: str | None = None,
    ) -> AgentRunRecord:
        """Build a persisted-friendly run record for a task-scoped agent."""
        instance = self.resolve_instance(
            role=role,
            scope_type="task",
            scope_id=task_id,
            provider_kind=provider_kind,
            runtime_mode=runtime_mode,
        )
        return self.create_run_record(
            agent=instance,
            task_id=task_id,
            branch=branch,
            worktree_path=worktree_path,
            prompt=prompt,
            skills=skills,
            retry_count=retry_count,
            max_retries=max_retries,
        )

    def create_run_record(
        self,
        *,
        agent: AgentInstanceRecord,
        task_id: str,
        branch: str | None,
        worktree_path: str | None,
        prompt: str | None = None,
        skills: list[str] | None = None,
        retry_count: int = 0,
        max_retries: int = 3,
    ) -> AgentRunRecord:
        """Build one execution run beneath a stable agent instance."""
        run_id = f"run-{agent.identity.agent_id}-{uuid4().hex[:8]}"
        native_log = self.vibrant_dir / "logs" / "providers" / "native" / f"{run_id}.ndjson"
        canonical_log = self.vibrant_dir / "logs" / "providers" / "canonical" / f"{run_id}.ndjson"
        provider = AgentProviderMetadata(
            kind=agent.provider.kind,
            transport=agent.provider.transport,
            runtime_mode=agent.provider.runtime_mode,
            native_event_log=str(native_log),
            canonical_event_log=str(canonical_log),
        )
        return AgentRunRecord(
            identity={
                "run_id": run_id,
                "agent_id": agent.identity.agent_id,
                "task_id": task_id,
                "role": agent.identity.role,
            },
            lifecycle={"status": AgentStatus.SPAWNING},
            context={
                "branch": branch,
                "worktree_path": worktree_path,
                "prompt_used": prompt,
                "skills_loaded": list(skills or []),
            },
            retry={
                "retry_count": retry_count,
                "max_retries": max_retries,
            },
            provider=provider,
        )

    def create_merge_agent_record(self, *, task_id: str, branch: str, worktree_path: str) -> AgentRunRecord:
        """Build a merge-agent run record for conflict-resolution flows."""
        return self.create_task_agent_record(
            role="merge",
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
        """Build a read-only validation/test agent run record."""
        return self.create_task_agent_record(
            role="test",
            task_id=task_id,
            branch=branch,
            worktree_path=worktree_path,
            prompt=prompt,
        )

    def _reconcile_instances_from_runs(self) -> None:
        for record in self.agent_store.list_records():
            instance = self.ensure_instance_for_run(record)
            instance.mark_run_updated(record)
            self.instance_store.upsert(instance)



def _scope_key(scope_type: str, scope_id: str | None) -> str:
    if scope_id is None:
        return scope_type.strip().lower()
    raw_scope = f"{scope_type}:{scope_id}"
    digest = hashlib.sha1(raw_scope.encode("utf-8")).hexdigest()[:8]
    return f"{_slug(scope_id)}-{digest}"



def _slug(value: str) -> str:
    cleaned = [character.lower() if character.isalnum() else "-" for character in value.strip()]
    collapsed = "".join(cleaned).strip("-")
    while "--" in collapsed:
        collapsed = collapsed.replace("--", "-")
    if not collapsed:
        raise ValueError("scope id must not be empty")
    return collapsed
